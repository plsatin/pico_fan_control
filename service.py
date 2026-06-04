"""Сервис сбора метрик GPU (nvidia-smi) и вентилятора (Pico), запись в InfluxDB,
веб-дашборд с трансляцией данных через WebSocket и автоматическое управление
скоростью вентилятора по **температуре GPU**.

Контур управления:
    nvidia-smi --> shared GPU temp --> compute_target_duty --> PWM --> Pico

Температура DS18B20 на Pico отображается для мониторинга, но **не управляет**
вентилятором. Это разделение нужно, чтобы вентилятор реагировал именно на
нагрев GPU, а не на собственный нагрев рядом с корпусом.

Конфигурация - через переменные окружения (см. .env.example). Параметры
кривой вентилятора также доступны через HTTP API и WebSocket.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import serial
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO
from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError, InfluxDBServerError

from fan_control import (
    DEFAULT_BAUD_RATE,
    DEFAULT_TIMEOUT,
    FanController,
    detect_port,
)

InfluxDBError = (InfluxDBClientError, InfluxDBServerError)

load_dotenv()

logger = logging.getLogger("service")


# ---------- Config (env) ----------
def _get(name: str, default: Optional[str] = None, cast: Any = str) -> Any:
    raw = os.getenv(name)
    if raw is None:
        if default is None:
            raise RuntimeError(f"Переменная окружения {name!r} не задана")
        raw = default
    if cast is str:
        return raw
    try:
        return cast(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Некорректное значение {name}={raw!r}: ожидается {cast.__name__}"
        ) from exc


INFLUX_HOST = _get("INFLUXDB_HOST", "127.0.0.1")
INFLUX_PORT = _get("INFLUXDB_PORT", "8086", int)
INFLUX_USER = _get("INFLUXDB_USER", "")
INFLUX_PASSWORD = _get("INFLUXDB_PASSWORD", "")
INFLUX_DB = _get("INFLUXDB_DB", "icinga2")
INFLUX_MEAS_FAN = _get("INFLUXDB_MEAS_FAN", "smart-fan")
INFLUX_MEAS_GPU = _get("INFLUXDB_MEAS_GPU", "docker-01")

POLL_INTERVAL = _get("POLL_INTERVAL", "2.0", float)
HTTP_HOST = _get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = _get("HTTP_PORT", "3000", int)
GPU_TAG = _get("GPU_TAG", "GPU0")
LOG_LEVEL = _get("LOG_LEVEL", "INFO").upper()

FAN_PORT = os.getenv("FAN_PORT") or None
FAN_BAUDRATE = _get("FAN_BAUDRATE", str(DEFAULT_BAUD_RATE), int)
FAN_TIMEOUT = _get("FAN_TIMEOUT", str(DEFAULT_TIMEOUT), float)

# Fan control defaults (overridable at runtime through /api/config).
# Defaults рассчитаны на температуру GPU (nvidia-smi), не на DS18B20.
FAN_TEMP_MIN = _get("FAN_TEMP_MIN", "50.0", float)
FAN_TEMP_MAX = _get("FAN_TEMP_MAX", "75.0", float)
FAN_PWM_MIN = _get("FAN_PWM_MIN", "30", int)
FAN_PWM_MAX = _get("FAN_PWM_MAX", "100", int)
FAN_HYSTERESIS = _get("FAN_HYSTERESIS", "3.0", float)
FAN_MODE = _get("FAN_MODE", "auto", str).lower()
FAN_MANUAL_PWM = _get("FAN_MANUAL_PWM", "0", int)

FLASK_SECRET = _get("FLASK_SECRET", None) or os.urandom(16).hex()

PWM_DEADBAND = 200  # ~0.3% от 65535


# ---------- Fan control config (mutable, thread-safe) ----------
@dataclass
class FanControlConfig:
    mode: str = "auto"           # "auto" | "manual"
    temp_min: float = 50.0       # °C GPU - порог включения
    temp_max: float = 75.0       # °C GPU - порог полных оборотов
    pwm_min: int = 30            # % - минимальный ШИМ
    pwm_max: int = 100           # % - максимальный ШИМ
    hysteresis: float = 3.0      # °C - запас от temp_min при остывании
    manual_pwm: int = 0          # % - ШИМ в ручном режиме

    def validate(self) -> None:
        if self.mode not in ("auto", "manual"):
            raise ValueError(f"mode должен быть 'auto' или 'manual'")
        if not (-50 <= self.temp_min <= 150):
            raise ValueError(f"temp_min {self.temp_min} вне [-50..150]")
        if not (-50 <= self.temp_max <= 150):
            raise ValueError(f"temp_max {self.temp_max} вне [-50..150]")
        if self.temp_min >= self.temp_max:
            raise ValueError("temp_min должен быть < temp_max")
        if not (0 <= self.pwm_min <= 100):
            raise ValueError("pwm_min должен быть 0..100")
        if not (0 <= self.pwm_max <= 100):
            raise ValueError("pwm_max должен быть 0..100")
        if self.pwm_min > self.pwm_max:
            raise ValueError("pwm_min должен быть <= pwm_max")
        if not (0 <= self.hysteresis <= 20):
            raise ValueError("hysteresis должен быть 0..20")
        if not (0 <= self.manual_pwm <= 100):
            raise ValueError("manual_pwm должен быть 0..100")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pwm_min_duty"] = self._pct_to_duty(self.pwm_min)
        d["pwm_max_duty"] = self._pct_to_duty(self.pwm_max)
        d["manual_pwm_duty"] = self._pct_to_duty(self.manual_pwm)
        return d

    @staticmethod
    def _pct_to_duty(pct: int) -> int:
        return int(pct * 65535 / 100)


@dataclass
class FanLoopState:
    direction: int = 0   # -1 cooling, 0 init, 1 heating
    last_duty: int = 0


@dataclass
class SharedMetrics:
    """Потокобезопасное состояние между фоновыми задачами."""
    gpu_temperature: Optional[float] = None
    gpu_last_update: float = 0.0
    fan_last_pwm: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)


shared_metrics = SharedMetrics()


_control_lock = threading.RLock()
_control_config = FanControlConfig(
    mode=FAN_MODE,
    temp_min=FAN_TEMP_MIN,
    temp_max=FAN_TEMP_MAX,
    pwm_min=FAN_PWM_MIN,
    pwm_max=FAN_PWM_MAX,
    hysteresis=FAN_HYSTERESIS,
    manual_pwm=FAN_MANUAL_PWM,
)
try:
    _control_config.validate()
except ValueError as exc:
    logger.error("Некорректная начальная FAN-конфигурация (%s); сброс к defaults", exc)
    _control_config = FanControlConfig()


def get_config() -> FanControlConfig:
    with _control_lock:
        return FanControlConfig(**asdict(_control_config))


def update_config(updates: dict) -> FanControlConfig:
    if not isinstance(updates, dict):
        raise ValueError("Ожидается JSON-объект")
    with _control_lock:
        candidate = asdict(_control_config)
        for k, v in updates.items():
            if hasattr(_control_config, k):
                candidate[k] = v
        validated = FanControlConfig(**candidate)
        validated.validate()
        # Commit только если валидация прошла
        for k, v in asdict(validated).items():
            setattr(_control_config, k, v)
        return FanControlConfig(**asdict(_control_config))


def compute_target_duty(temp: float, cfg: FanControlConfig, state: FanLoopState) -> int:
    """Возвращает duty_cycle (0..65535) по кривой. Мутирует ``state.direction``."""
    if cfg.mode == "manual":
        state.direction = 0
        return FanControlConfig._pct_to_duty(cfg.manual_pwm)

    if temp >= cfg.temp_max:
        state.direction = 1
        return FanControlConfig._pct_to_duty(cfg.pwm_max)

    eff_min = cfg.temp_min - cfg.hysteresis if state.direction == -1 else cfg.temp_min

    if temp <= eff_min:
        state.direction = -1
        return FanControlConfig._pct_to_duty(cfg.pwm_min)

    ratio = (temp - eff_min) / (cfg.temp_max - eff_min)
    ratio = max(0.0, min(1.0, ratio))
    pwm_pct = cfg.pwm_min + ratio * (cfg.pwm_max - cfg.pwm_min)
    state.direction = 1 if ratio > 0.5 else -1
    return FanControlConfig._pct_to_duty(int(pwm_pct))


# ---------- Logging ----------
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# ---------- Flask + SocketIO ----------
app = Flask(__name__)
app.config["SECRET_KEY"] = FLASK_SECRET
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")


# ---------- InfluxDB ----------
def _make_influx_client() -> InfluxDBClient:
    kwargs: dict[str, Any] = {
        "host": INFLUX_HOST,
        "port": INFLUX_PORT,
        "database": INFLUX_DB,
    }
    if INFLUX_USER:
        kwargs["username"] = INFLUX_USER
    if INFLUX_PASSWORD:
        kwargs["password"] = INFLUX_PASSWORD
    return InfluxDBClient(**kwargs)


try:
    influx_client = _make_influx_client()
except Exception:
    logger.exception("Не удалось создать InfluxDB-клиент")
    raise


def write_influx(
    measurement: str, fields: dict, tags: Optional[dict] = None
) -> None:
    point = {
        "measurement": measurement,
        "tags": tags or {},
        "time": datetime.now(timezone.utc).isoformat(),
        "fields": fields,
    }
    try:
        if not influx_client.write_points([point]):
            logger.warning("InfluxDB: пустой ответ на запись в %s", measurement)
    except InfluxDBError as exc:
        logger.error("InfluxDB error: code=%s %s", getattr(exc, "code", "?"), exc)
    except Exception:
        logger.exception("InfluxDB: неожиданная ошибка записи в %s", measurement)


# ---------- Data collectors ----------
def fetch_gpu_info() -> Optional[dict]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu,utilization.memory,"
                "power.draw,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.debug("nvidia-smi не найден в PATH - GPU-метрики отключены")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("nvidia-smi: таймаут 5с")
        return None
    except subprocess.CalledProcessError as exc:
        logger.error(
            "nvidia-smi: код=%d stderr=%r", exc.returncode, exc.stderr.strip()
        )
        return None
    except Exception:
        logger.exception("nvidia-smi: неожиданная ошибка")
        return None

    values = [v.strip() for v in result.stdout.strip().split(",")]
    if len(values) < 6:
        logger.warning("nvidia-smi: неожиданный формат вывода: %r", result.stdout)
        return None
    try:
        return {
            "timestamp": datetime.now(timezone.utc).timestamp() * 1000,
            "temperature": float(values[0]),
            "gpuUtil": float(values[1]),
            "memUtil": float(values[2]),
            "power": float(values[3]),
            "memUsed": float(values[4]),
            "memFree": float(values[5]),
        }
    except (ValueError, IndexError) as exc:
        logger.warning("nvidia-smi: не удалось распарсить %r: %s", values, exc)
        return None


def parse_fan_response(response: str) -> Optional[dict]:
    """Ожидаемый формат: ``'<temperature>;<pwm_duty_cycle>'``."""
    if not response:
        return None
    try:
        temp_str, pwm_str = response.split(";", 1)
        temperature = float(temp_str)
        pwm = int(pwm_str)
    except (ValueError, AttributeError) as exc:
        logger.debug("parse_fan_response: %s (%r)", exc, response)
        return None
    return {
        "timestamp": datetime.now(timezone.utc).timestamp() * 1000,
        "temperature": temperature,
        "fanSpeedPercentage": round(pwm / 65535 * 100, 2),
        "pwm_duty": pwm,
    }


# ---------- Background tasks ----------
def background_gpu_task() -> None:
    logger.info("Старт опроса GPU, интервал %sс", POLL_INTERVAL)
    while True:
        try:
            data = fetch_gpu_info()
            if data is not None:
                tags = {"gpu": GPU_TAG}
                fields = {k: v for k, v in data.items() if k != "timestamp"}
                write_influx(INFLUX_MEAS_GPU, fields, tags)
                try:
                    socketio.emit("gpu_data", data)
                except Exception:
                    logger.exception("WebSocket emit gpu_data: ошибка")
                with shared_metrics._lock:
                    shared_metrics.gpu_temperature = data["temperature"]
                    shared_metrics.gpu_last_update = time.time()
        except Exception:
            logger.exception("background_gpu_task: ошибка итерации")
        socketio.sleep(POLL_INTERVAL)


def background_fan_task() -> None:
    """Управляющий цикл по **температуре GPU**.

    Всегда испускает ``fan_data`` (с текущим решением контура управления),
    даже если Pico недоступна. Это гарантирует, что график в веб-интерфейсе
    показывает Fan % и Fan target, а не только GPU-температуру.
    """
    logger.info("Старт контура управления вентилятором (по температуре GPU)")
    backoff = 1.0
    loop_state = FanLoopState()
    fc: Optional[ FanController] = None

    while True:
        try:
            cfg = get_config()
            with shared_metrics._lock:
                gpu_temp = shared_metrics.gpu_temperature
                gpu_last = shared_metrics.gpu_last_update
                last_pwm = shared_metrics.fan_last_pwm

            gpu_age = time.time() - gpu_last if gpu_last > 0 else float("inf")
            gpu_fresh = gpu_temp is not None and gpu_age < POLL_INTERVAL * 5

            if gpu_fresh:
                target_duty = compute_target_duty(gpu_temp, cfg, loop_state)
            else:
                # Нет свежих данных GPU - удерживаем последнее решение
                target_duty = last_pwm

            # Best-effort взаимодействие с Pico
            current_duty = last_pwm
            pico_temp: Optional[float] = None
            pico_ok = False

            if fc is None:
                try:
                    fc = FanController(
                        port=FAN_PORT,
                        baudrate=FAN_BAUDRATE,
                        timeout=FAN_TIMEOUT,
                    )
                    fc.open()
                    logger.info("Подключено к Pico на %s", fc.port)
                    backoff = 1.0
                except (serial.SerialException, OSError) as exc:
                    logger.debug("Pico connect: %s", exc)
                    fc = None
                except Exception:
                    logger.exception("Pico: неожиданная ошибка подключения")
                    fc = None

            if fc is not None:
                try:
                    response = fc.send_command("temperature,pwm")
                    parsed = parse_fan_response(response)
                    if parsed is not None:
                        current_duty = parsed["pwm_duty"]
                        pico_temp = parsed["temperature"]
                        pico_ok = True
                except TimeoutError as exc:
                    logger.warning("Pico read timeout: %s", exc)
                except (serial.SerialException, OSError) as exc:
                    logger.warning("Pico read failed: %s", exc)
                    try:
                        fc.close()
                    except Exception:
                        pass
                    fc = None
                    backoff = min(backoff * 2, 30.0)
                except Exception:
                    logger.exception("Pico: неожиданная ошибка чтения")

                if pico_ok and abs(target_duty - current_duty) > PWM_DEADBAND:
                    try:
                        fc.send_command(str(target_duty))
                        current_duty = target_duty
                        logger.debug(
                            "PWM: %d -> %d (GPU_T=%.1f°C, mode=%s)",
                            last_pwm, target_duty,
                            gpu_temp if gpu_fresh else float("nan"),
                            cfg.mode,
                        )
                    except (serial.SerialException, OSError) as exc:
                        logger.warning("Pico write failed: %s", exc)
                        try:
                            fc.close()
                        except Exception:
                            pass
                        fc = None
                        backoff = min(backoff * 2, 30.0)
                    except Exception:
                        logger.exception("Pico: неожиданная ошибка записи")

            with shared_metrics._lock:
                shared_metrics.fan_last_pwm = current_duty

            # Всегда испускаем fan_data (даже если Pico недоступна)
            fan_pct = round(current_duty / 65535 * 100, 2)
            target_pct = round(target_duty / 65535 * 100, 2)
            state = {
                "timestamp": time.time() * 1000,
                "gpuTemperature": gpu_temp if gpu_fresh else None,
                "picoTemperature": pico_temp,
                "pwm_duty": current_duty,
                "target_duty": target_duty,
                "fanSpeedPercentage": fan_pct,
                "targetPct": target_pct,
                "mode": cfg.mode,
            }

            tags = {"gpu": GPU_TAG}
            fields = {
                "fanSpeedPercentage": fan_pct,
                "targetPct": target_pct,
                "pwm_duty": current_duty,
                "target_duty": target_duty,
            }
            if gpu_fresh:
                fields["gpuTemperature"] = gpu_temp
                fields["temperature"] = gpu_temp  # backward compat
            if pico_temp is not None:
                fields["picoTemperature"] = pico_temp
            write_influx(INFLUX_MEAS_FAN, fields, tags)

            try:
                socketio.emit("fan_data", state)
            except Exception:
                logger.exception("WebSocket emit fan_data: ошибка")

            if fc is None:
                socketio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            else:
                socketio.sleep(POLL_INTERVAL)
        except Exception:
            logger.exception("background_fan_task: ошибка итерации")
            socketio.sleep(min(backoff, 30.0))
            backoff = min(backoff * 2, 30.0)


# ---------- HTTP routes ----------
@app.route("/")
def index() -> Any:
    try:
        return send_from_directory(os.path.abspath("."), "index.html")
    except FileNotFoundError:
        return "index.html not found in service directory", 404


@app.route("/health")
def health() -> Any:
    return {"status": "ok"}


@app.route("/api/config", methods=["GET"])
def api_get_config() -> Any:
    return jsonify(get_config().to_dict())


@app.route("/api/config", methods=["POST"])
def api_set_config() -> Any:
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as exc:
        return jsonify({"error": f"Некорректный JSON: {exc}"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидается JSON-объект"}), 400
    try:
        new_cfg = update_config(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("update_config failed")
        return jsonify({"error": f"Внутренняя ошибка: {exc}"}), 500
    try:
        socketio.emit("config_update", new_cfg.to_dict())
    except Exception:
        logger.exception("WebSocket emit config_update: ошибка")
    logger.info(
        "Fan config updated: mode=%s temp=[%.1f..%.1f] pwm=[%d..%d] hyst=%.1f manual=%d",
        new_cfg.mode, new_cfg.temp_min, new_cfg.temp_max,
        new_cfg.pwm_min, new_cfg.pwm_max,
        new_cfg.hysteresis, new_cfg.manual_pwm,
    )
    return jsonify(new_cfg.to_dict())


# ---------- Main ----------
def main() -> int:
    logger.info("InfluxDB target: %s:%s/%s", INFLUX_HOST, INFLUX_PORT, INFLUX_DB)
    try:
        influx_client.create_database(INFLUX_DB)
        logger.info("InfluxDB: база %s готова", INFLUX_DB)
    except InfluxDBError as exc:
        if getattr(exc, "code", None) == 409:
            logger.info("InfluxDB: база %s уже существует", INFLUX_DB)
        else:
            logger.warning("InfluxDB: create_database: %s", exc)
    except Exception:
        logger.exception("InfluxDB: ошибка инициализации")

    try:
        probe = detect_port(FAN_PORT)
        logger.info("Обнаружен порт Pico: %s", probe)
    except serial.SerialException as exc:
        logger.warning(
            "FanController при старте: %s (фоновый поток будет ретраить)", exc
        )

    logger.info(
        "Fan control: mode=%s T=[%.1f..%.1f]°C PWM=[%d..%d]%% hyst=%.1f°C manual=%d%%",
        _control_config.mode, _control_config.temp_min, _control_config.temp_max,
        _control_config.pwm_min, _control_config.pwm_max,
        _control_config.hysteresis, _control_config.manual_pwm,
    )

    socketio.start_background_task(background_gpu_task)
    socketio.start_background_task(background_fan_task)

    try:
        socketio.run(
            app,
            host=HTTP_HOST,
            port=HTTP_PORT,
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except OSError as exc:
        logger.error(
            "Не удалось запустить HTTP-сервер на %s:%s: %s",
            HTTP_HOST, HTTP_PORT, exc,
        )
        return 1
    except Exception:
        logger.exception("Критическая ошибка сервера")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
