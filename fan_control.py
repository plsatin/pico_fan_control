"""fan_control - кросс-платформенный модуль управления вентилятором Raspberry Pi Pico.

Использование в качестве библиотеки:

    from fan_control import FanController
    with FanController() as fc:
        print(fc.send_command("temperature"))
        print(fc.send_command("temperature,pwm"))

Использование в качестве CLI:

    python -m fan_control temperature
    python -m fan_control temperature,pwm
    python -m fan_control 32768 -p COM5
    python -m fan_control pwm -p /dev/ttyACM0
    python -m fan_control --list-ports
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Optional

import serial
from serial.tools import list_ports

__all__ = [
    "FanController",
    "detect_port",
    "DEFAULT_BAUD_RATE",
    "DEFAULT_TIMEOUT",
    "main",
]

DEFAULT_BAUD_RATE = 115200
DEFAULT_TIMEOUT = 2.0

# USB VID/PID Raspberry Pi Pico (RP2 bootloader и CircuitPython CDC)
_RPI_VID = 0x2E8A
_RPI_PIDS = {0x0003, 0x000A, 0x000B, 0x000C, 0x000D, 0x000E, 0x000F}

logger = logging.getLogger(__name__)


def detect_port(preferred: Optional[str] = None) -> str:
    """Возвращает имя порта Pico.

    Приоритет:
      1. ``preferred`` (если указан);
      2. порт с VID/PID Raspberry Pi;
      3. первый доступный порт.

    Поднимает :class:`serial.SerialException`, если порт не найден.
    """
    if preferred:
        return preferred

    ports = list(list_ports.comports())
    if not ports:
        raise serial.SerialException(
            "Не найдено ни одного последовательного порта"
        )

    for p in ports:
        if p.vid == _RPI_VID and p.pid in _RPI_PIDS:
            logger.debug(
                "Найден Pico: %s (VID=%04X PID=%04X)", p.device, p.vid, p.pid
            )
            return p.device

    logger.debug(
        "Pico не найден по VID/PID, использую первый порт: %s", ports[0].device
    )
    return ports[0].device


class FanController:
    """Высокоуровневая обёртка над UART-соединением с Pico."""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = DEFAULT_BAUD_RATE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.port = detect_port(port)
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None

    def open(self) -> "FanController":
        logger.debug("Открываю порт %s @ %d", self.port, self.baudrate)
        self._ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        return self

    def close(self) -> None:
        if self._ser is not None and self._ser.is_open:
            logger.debug("Закрываю порт %s", self.port)
            self._ser.close()
        self._ser = None

    def __enter__(self) -> "FanController":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def send_command(self, command: str) -> str:
        """Отправляет текстовую команду Pico и возвращает ответ одной строкой.

        Пробелы по краям обрезаются, эхо самой команды (если REPL Pico его
        возвращает) пропускается.
        """
        if self._ser is None or not self._ser.is_open:
            raise serial.SerialException(
                "Порт не открыт. Используйте контекстный менеджер или .open()."
            )

        cmd = command.strip()
        if not cmd:
            raise ValueError("Пустая команда")

        payload = (cmd + "\r\n").encode("utf-8")
        logger.debug("-> %r", payload)
        self._ser.write(payload)
        self._ser.flush()

        # Считываем все доступные строки в пределах таймаута
        deadline = time.monotonic() + self.timeout
        lines: list[str] = []
        while time.monotonic() < deadline:
            raw = self._ser.readline()
            if not raw:
                break
            text = raw.decode("utf-8", errors="ignore").strip()
            if text:
                lines.append(text)
                logger.debug("<- %r", text)

        if not lines:
            raise TimeoutError(
                f"Устройство на {self.port} не ответило за {self.timeout} с"
            )

        # Пропускаем эхо команды, если REPL Pico вернул её обратно
        meaningful = [ln for ln in lines if ln != cmd]
        return meaningful[-1] if meaningful else lines[-1]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fan_control",
        description="Отправляет команду Pico и печатает ответ в stdout.",
    )
    p.add_argument(
        "command",
        nargs="?",
        default="temperature",
        help="Команда для Pico (по умолчанию: temperature).",
    )
    p.add_argument(
        "-p",
        "--port",
        help="COM-порт или tty-устройство (по умолчанию - автоопределение).",
    )
    p.add_argument(
        "-b",
        "--baudrate",
        type=int,
        default=DEFAULT_BAUD_RATE,
        help=f"Скорость UART (по умолчанию: {DEFAULT_BAUD_RATE}).",
    )
    p.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Таймаут чтения, с (по умолчанию: {DEFAULT_TIMEOUT}).",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Подробный вывод (логирование в stderr).",
    )
    p.add_argument(
        "--list-ports",
        action="store_true",
        help="Показать доступные COM-порты и выйти.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.list_ports:
        for p in list_ports.comports():
            vid = f"{p.vid:04X}" if p.vid is not None else "----"
            pid = f"{p.pid:04X}" if p.pid is not None else "----"
            print(f"{p.device}: {p.description} [VID={vid} PID={pid}]")
        return 0

    try:
        with FanController(
            port=args.port,
            baudrate=args.baudrate,
            timeout=args.timeout,
        ) as fc:
            try:
                response = fc.send_command(args.command)
            except TimeoutError as exc:
                print(f"Ошибка: {exc}", file=sys.stderr)
                return 2
    except serial.SerialException as exc:
        print(f"Ошибка порта: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    print(response)
    return 0


if __name__ == "__main__":
    sys.exit(main())
