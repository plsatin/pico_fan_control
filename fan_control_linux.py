import time
import serial
import sys

PORT = "/dev/ttyACM0"  # Измените на ваш порт (/dev/ttyUSB0, COM3 и т.д.)
BAUD_RATE = 115200


try:
    # Обязательно указываем timeout, чтобы программа не зависла, если устройство не ответит
    with serial.Serial(PORT, BAUD_RATE, timeout=2) as ser:
#        print(f"Порт {PORT} открыт.")

        # Очищаем буферы перед началом работы
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # 1. Формируем команду
        # Большинство устройств ждут в конце символ переноса строки (\n или \r\n)
        #command = "GET_DATA\n"

        arg_value = str(sys.argv[1]) + '\r\n'
        # command = arg_value.to_bytes(2, byteorder='big')
        command = arg_value.encode("utf-8")



#        print(f"Отправляем команду: {command.strip()}")

        # 2. Отправляем данные (текст нужно обязательно перевести в байты: .encode())
        ser.write(command)

        # Ждем, пока все байты физически уйдут из буфера отправки в порт
        ser.flush()

        # Небольшая пауза, чтобы устройство успело обработать команду и ответить
        time.sleep(0.1)

        # 3. Читаем ответ
        # Метод readline() будет ждать ответа до тех пор, пока не встретит '\n'
        # или пока не выйдет время таймаута (в нашем случае 2 секунды)
        response_bytes = ser.readline()
        response_bytes = ser.readline()
        if response_bytes:
            # Декодируем байты обратно в текст и убираем лишние пробелы/переносы
            response_text = response_bytes.decode("utf-8").strip()
            print(f"{response_text}")
        else:
            print("Ошибка: Устройство не ответило вовремя (таймаут).")

except serial.SerialException as e:
    print(f"Ошибка порта: {e}")
