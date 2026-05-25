## Датчик шума на Pico (code_noise_sensor.py)

import time
import board
import analogio

# Настройка аналогового входа на пин GP26 (ADC0)
mic = analogio.AnalogIn(board.GP26)

# Длительность окна замера в секундах (50 мс)
SAMPLE_WINDOW = 0.05

while True:
    start_time = time.monotonic()
    
    # Инициализируем переменные экстремумов
    signal_min = 65535
    signal_max = 0
    
    # Собираем данные в течение 50 миллисекунд
    while time.monotonic() - start_time < SAMPLE_WINDOW:
        sample = mic.value
        
        # Поиск минимального значения волны
        if sample < signal_min:
            signal_min = sample
            
        # Поиск максимального значения волны
        if sample > signal_max:
            signal_max = sample
            
    # Вычисляем размах амплитуды (Peak-to-Peak)
    peak_to_peak = signal_max - signal_min
    
    # Переводим амплитуду в вольты (от 0.0 В до 3.3 В)
    volts = (peak_to_peak * 3.3) / 65535
    
    # Вывод уровня шума в консоль
    # Можно открыть Plotter в Mu Editor для просмотра графика
    print((volts,))
    
    # Небольшая пауза между замерами шума
    time.sleep(0.1)
