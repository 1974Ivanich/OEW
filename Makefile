# Makefile for STM32G474RE OEW Motor FOC
TOOLCHAIN = arm-none-eabi-
CC = $(TOOLCHAIN)gcc
AS = $(TOOLCHAIN)gcc -x assembler-with-cpp
OBJCOPY = $(TOOLCHAIN)objcopy
SIZE = $(TOOLCHAIN)size

CMSIS_DIR = Drivers/CMSIS
CMSIS_DEVICE_DIR = $(CMSIS_DIR)/Device/ST/STM32G4xx
CMSIS_CORE_DIR = $(CMSIS_DIR)/Include

TOOLCHAIN_PATH = C:\ST\STM32CubeCLT_1.22.0\GNU-tools-for-STM32\bin
MAKE_PATH = C:\ST\STM32CubeCLT_1.22.0\Make\bin
export PATH := $(TOOLCHAIN_PATH);$(MAKE_PATH);$(PATH)

TARGET = firmware
BUILD_DIR = build

# Source files
SRC_DIR = src
C_SOURCES = \
main.c \
system_stm32g4xx.c \
$(SRC_DIR)/pwm.c \
$(SRC_DIR)/adc.c \
$(SRC_DIR)/uart.c \
$(SRC_DIR)/cordic_math.c \
$(SRC_DIR)/foc.c \
$(SRC_DIR)/observer.c \
$(SRC_DIR)/pll.c \
$(SRC_DIR)/flux_weakening.c \
$(SRC_DIR)/voltage_manager.c \
$(SRC_DIR)/vf_start.c \
$(SRC_DIR)/protect.c \
$(SRC_DIR)/autotune.c \
$(SRC_DIR)/encoder.c \
$(SRC_DIR)/vf_control.c \
$(SRC_DIR)/swo.c \
$(SRC_DIR)/control_isr.c \
$(SRC_DIR)/current_reconstruct.c \
$(SRC_DIR)/foc_handoff_gate.c \
$(SRC_DIR)/pwm_board_pins.c

ASM_SOURCES = startup_stm32g474xx.s

# Includes
INCLUDES = \
-I. \
-I$(SRC_DIR) \
-I$(CMSIS_DEVICE_DIR)/Include \
-I$(CMSIS_CORE_DIR)

CPU_FLAGS = -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard
OPT = -Os
CFLAGS = $(CPU_FLAGS) $(OPT) $(INCLUDES) -Wall -Wextra -Wno-unused-parameter
CFLAGS += -DSTM32G474xx
CFLAGS += -ffunction-sections -fdata-sections -std=c99
CFLAGS += -Werror=misleading-indentation -Werror=implicit-function-declaration
LDFLAGS = $(CPU_FLAGS) -Tlinker.ld -Wl,-Map=$(BUILD_DIR)/$(TARGET).map
LDFLAGS += -Wl,--gc-sections -Wl,--start-group -lc -lm -Wl,--end-group
LDFLAGS += -specs=nano.specs -specs=nosys.specs -u _printf_float

C_OBJECTS = $(addprefix $(BUILD_DIR)/, $(C_SOURCES:.c=.o))
ASM_OBJECTS = $(addprefix $(BUILD_DIR)/, $(ASM_SOURCES:.s=.o))
OBJECTS = $(C_OBJECTS) $(ASM_OBJECTS)

VPATH = $(SRC_DIR)

all: $(BUILD_DIR)/$(TARGET).elf $(BUILD_DIR)/$(TARGET).bin
	@echo "Build complete!"
	$(SIZE) $(BUILD_DIR)/$(TARGET).elf

$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) linker.ld
	@mkdir -p $(BUILD_DIR)
	$(CC) $(OBJECTS) $(LDFLAGS) -o $@

$(BUILD_DIR)/%.o: %.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: %.s
	@mkdir -p $(BUILD_DIR)
	$(AS) $(ASFLAGS) -c $< -o $@

$(BUILD_DIR)/$(TARGET).bin: $(BUILD_DIR)/$(TARGET).elf
	$(OBJCOPY) -O binary $< $@

clean:
	rm -rf $(BUILD_DIR)

flash: $(BUILD_DIR)/$(TARGET).bin
	"C:\ST\STM32CubeCLT_1.22.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe" -c port=SWD mode=UR -w $(BUILD_DIR)/$(TARGET).bin 0x08000000 -v -rst

.PHONY: all clean flash test test-hosted test-qemu

# ── Тесты FOC/Vf математики (hosted + QEMU, без железа) ────────────────────
HOSTED_GCC = gcc
ARM_GCC = arm-none-eabi-gcc
QEMU = C:/ST/xpack-qemu-arm-9.2.4-1/bin/qemu-system-arm.exe
MOCK_INC = -I tests/mocks
TEST_COMMON = tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/foc_handoff_gate.c

test: test-hosted test-qemu
	@echo "=== TESTS OK ==="

test-hosted: tests/foc_test_hosted.exe tests/vf_test_hosted.exe tests/cordic_mod_test.exe tests/vm_test_hosted.exe tests/control_isr_test.exe tests/foc_handoff_gate_test.exe tests/adc_frame_host_test.exe tests/current_reconstruct_test.exe
	@echo "--- FOC math (hosted) ---"; ./tests/foc_test_hosted.exe
	@echo "--- V/f control (hosted) ---"; ./tests/vf_test_hosted.exe
	@echo "--- CORDIC Modulus (hosted) ---"; ./tests/cordic_mod_test.exe
	@echo "--- Voltage Manager (hosted) ---"; ./tests/vm_test_hosted.exe
	@echo "--- ADC ISR decisions (hosted) ---"; ./tests/control_isr_test.exe
	@echo "--- FOC handoff gate (hosted) ---"; ./tests/foc_handoff_gate_test.exe
	@echo "--- ADC frame dual (hosted) ---"; ./tests/adc_frame_host_test.exe
	@echo "--- Current reconstruct (hosted) ---"; ./tests/current_reconstruct_test.exe

test-qemu: tests/foc_test_qemu.elf tests/vf_test_qemu.elf
	@echo "--- FOC math (QEMU) ---"; $(QEMU) -M olimex-stm32-h405 -nographic -semihosting-config enable=on,target=native -kernel tests/foc_test_qemu.elf 2>&1 | tail -3
	@echo "--- V/f control (QEMU) ---"; $(QEMU) -M olimex-stm32-h405 -nographic -semihosting-config enable=on,target=native -kernel tests/vf_test_qemu.elf 2>&1 | tail -3

tests/foc_test_hosted.exe: tests/foc_math_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/foc_math_test.c $(TEST_COMMON) tests/mocks/vfc_stub.c -lm -o $@

tests/vf_test_hosted.exe: tests/vf_control_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/vf_control_test.c tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/vf_control.c -o $@

tests/cordic_mod_test.exe: tests/cordic_mod_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/cordic_mod_test.c -o $@

tests/vm_test_hosted.exe: tests/vm_test.c tests/mocks/mock_cordic.c src/voltage_manager.c src/voltage_manager.h
	$(HOSTED_GCC) -I src $(MOCK_INC) tests/vm_test.c tests/mocks/mock_cordic.c src/voltage_manager.c -o $@

# Ревью TEST-03: portabled ISR-решения и handoff-gate (не требуют STM32)
tests/control_isr_test.exe: tests/control_isr_test.c src/control_isr.c src/control_isr.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/control_isr.c tests/control_isr_test.c -o $@

tests/foc_handoff_gate_test.exe: tests/foc_handoff_gate_test.c src/foc_handoff_gate.c src/foc_handoff_gate.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/foc_handoff_gate.c tests/foc_handoff_gate_test.c -o $@

# Ревью ADC-2S: dual injected simultaneous AdcFrame (мок регистров в mocks_adc)
tests/adc_frame_host_test.exe: tests/adc_frame_host_test.c src/adc.c src/adc.h tests/mocks_adc/stm32g474xx.h tests/mocks_adc/registers.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc -Itests/mocks_adc src/adc.c tests/adc_frame_host_test.c tests/mocks_adc/registers.c -o $@

tests/foc_test_qemu.elf: tests/foc_math_test.c tests/qemu_startup.s tests/qemu_test.ld
	$(ARM_GCC) -mcpu=cortex-m4 -mthumb -mfloat-abi=soft $(MOCK_INC) -I src -ffunction-sections -fdata-sections tests/qemu_startup.s tests/foc_math_test.c $(TEST_COMMON) tests/mocks/vfc_stub.c -Wl,--gc-sections -T tests/qemu_test.ld -nostdlib -lgcc -o $@

tests/vf_test_qemu.elf: tests/vf_control_test.c tests/qemu_startup.s tests/qemu_test.ld
	$(ARM_GCC) -mcpu=cortex-m4 -mthumb -mfloat-abi=soft $(MOCK_INC) -I src -ffunction-sections -fdata-sections tests/qemu_startup.s tests/vf_control_test.c tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/vf_control.c -Wl,--gc-sections -T tests/qemu_test.ld -nostdlib -lgcc -o $@

tests/current_reconstruct_test.exe: tests/current_reconstruct_test.c src/current_reconstruct.c src/current_reconstruct.h src/adc.h tests/adc_frame_stub.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/current_reconstruct.c tests/current_reconstruct_test.c tests/adc_frame_stub.c -o $@
