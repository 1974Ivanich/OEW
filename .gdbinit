target remote localhost:3333
monitor reset halt

# Загрузить прошивку
load build/firmware.elf

# Сбросить и остановить на main
monitor reset halt
break main
continue
