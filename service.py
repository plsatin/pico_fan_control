"""Сервис сбора метрик GPU (nvidia-smi) и вентилятора (Pico), запись в InfluxDB,
веб-дашборд с трансляцией данных через WebSocket.

Конфигурация - через переменные окружения. Файл .env читается автоматически
(см. .env.example для шаблона). Все параметры можно переопределить через
реальное окружение, например::

    INFLUXDB_HOST=10.0.0.5 python service.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import serial
from dotenv import load_dotenv
from flask import Flask, send_from_directory
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


# ---------- Config ----------
def _get(name: str, default: Optional[str] = None, cast: Any = str) -> Any:
    """Читает переменную окружения и приводит к нужному типу.

    Бросает :class:`RuntimeError` с понятным сообщением, если значение
    не задано (и нет default) или не приводится к ``cast``.
    """
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

FLASK_SECRET = _get("FLASK_SECRET", None) or os.urandom(16).hex()


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
    """Ожидаемый формат от Pico: ``'<temperature>;<pwm_duty_cycle>'``."""
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
        except Exception:
            logger.exception("background_gpu_task: ошибка итерации")
        socketio.sleep(POLL_INTERVAL)


def background_fan_task() -> None:
    logger.info("Старт опроса вентилятора")
    backoff = 1.0
    while True:
        try:
            with FanController(
                port=FAN_PORT,
                baudrate=FAN_BAUDRATE,
                timeout=FAN_TIMEOUT,
            ) as fc:
                logger.info("Подключено к Pico на %s", fc.port)
                backoff = 1.0
                while True:
                    try:
                        response = fc.send_command("temperature,pwm")
                        data = parse_fan_response(response)
                    except TimeoutError as exc:
                        logger.warning("Pico: %s", exc)
                        data = None
                    except (serial.SerialException, OSError) as exc:
                        logger.warning("Pico: потеря связи: %s", exc)
                        break
                    except Exception:
                        logger.exception("Pico: неожиданная ошибка")
                        data = None

                    if data is not None:
                        tags = {"gpu": GPU_TAG}
                        fields = {
                            k: v for k, v in data.items() if k != "timestamp"
                        }
                        write_influx(INFLUX_MEAS_FAN, fields, tags)
                    socketio.sleep(POLL_INTERVAL)
        except Exception:
            logger.exception(
                "background_fan_task: переподключение через %.1fс", backoff
            )
        socketio.sleep(backoff)
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
            HTTP_HOST,
            HTTP_PORT,
            exc,
        )
        return 1
    except Exception:
        logger.exception("Критическая ошибка сервера")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
