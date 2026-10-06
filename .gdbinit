# GDB-инициализация для отладки OEW (запускается `make debug`).
#
# Подключение к OpenOCD выполняет Makefile до этого файла:
#   arm-none-eabi-gdb -q -ex "target remote localhost:3333" -x .gdbinit firmware.elf
# При ручном запуске GDB сначала подключись сам:
#   target remote localhost:3333

# Загрузить прошивку и начать с точки входа
load build/firmware.elf
monitor reset halt

# Точка останова на main и пуск
break main
continue

# G (run) — отсоединиться от цели и дать ей работать свободно
# q (quit)  — отсоединиться и выйти (цель продолжает работать)
define G
  detach
  monitor reset run
  continue
end

# Подробнее о командах OpenOCD: monitor help
