import sys
import serial
import time

def main():
    if len(sys.argv) < 2:
        print("Использование: python read_uart.py <COM_PORT>")
        print("Пример: python read_uart.py COM15")
        return

    port = sys.argv[1]
    baudrate = 115200

    print(f"Открываю порт {port} на скорости {baudrate}...")
    
    try:
        ser = serial.Serial(port, baudrate, timeout=1)
        print("Порт открыт. Ожидание данных от платы...\n")
        
        start_time = time.time()
        max_duration = 15.0  # 15 секунд
        max_lines = 30      # или 30 строк
        line_count = 0
        
        while True:
            # Проверяем условия выхода
            elapsed = time.time() - start_time
            if elapsed >= max_duration:
                print(f"\n--- Прошло {max_duration} секунд. Завершаю приём. ---")
                break
            if line_count >= max_lines:
                print(f"\n--- Получено {max_lines} строк. Завершаю приём. ---")
                break
            
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    print(line)
                    line_count += 1
            else:
                time.sleep(0.05)
            
    except serial.SerialException as e:
        print(f"Ошибка COM-порта: {e}")
    except KeyboardInterrupt:
        print("\nПрограмма остановлена пользователем.")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("Порт закрыт.")

if __name__ == "__main__":
    main()
