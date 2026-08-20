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
$(SRC_DIR)/current_map_selector.c \
$(SRC_DIR)/map_builder.c \
$(SRC_DIR)/map_capture.c \
$(SRC_DIR)/map_capture_port.c \
$(SRC_DIR)/map_capture_profiles.c \
$(SRC_DIR)/foc_handoff_gate.c \
$(SRC_DIR)/foc_run_policy.c \
$(SRC_DIR)/foc_slip_policy.c \
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
CFLAGS += -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u
EXTRA_CFLAGS ?=
CFLAGS += $(EXTRA_CFLAGS)
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

.PHONY: all clean flash test test-hosted test-qemu pwm_hs1_default_deny

# ── Тесты FOC/Vf математики (hosted + QEMU, без железа) ────────────────────
HOSTED_GCC = gcc
ARM_GCC = arm-none-eabi-gcc
QEMU ?= C:/ST/xpack-qemu-arm-9.2.4-1/bin/qemu-system-arm.exe
MOCK_INC = -I tests/mocks
TEST_COMMON = tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/foc_handoff_gate.c src/foc_run_policy.c src/foc_slip_policy.c src/current_reconstruct.c src/current_map_selector.c

test: test-hosted test-qemu
	@echo "=== TESTS OK ==="

test-hosted: tests/foc_test_hosted.exe tests/vf_test_hosted.exe tests/cordic_mod_test.exe tests/vm_test_hosted.exe tests/control_isr_test.exe tests/foc_handoff_gate_test.exe tests/foc_run_policy_test.exe tests/foc_slip_policy_test.exe tests/adc_frame_host_test.exe tests/current_reconstruct_test.exe tests/pwm_hs1_test.exe tests/pwm_break_init_test.exe tests/foc_start_gate_test.exe tests/protect_frame_host_test.exe tests/current_map_selector_test.exe tests/map_capture_test.exe tests/map_capture_port_test.exe tests/sd_interlock_test.exe tests/sd_latch_test.exe tests/sd_no_self_rearm_test.exe tests/map_builder_test.exe

	@echo "--- FOC math (hosted) ---"; ./tests/foc_test_hosted.exe
	@echo "--- V/f control (hosted) ---"; ./tests/vf_test_hosted.exe
	@echo "--- CORDIC Modulus (hosted) ---"; ./tests/cordic_mod_test.exe
	@echo "--- Voltage Manager (hosted) ---"; ./tests/vm_test_hosted.exe
	@echo "--- ADC ISR decisions (hosted) ---"; ./tests/control_isr_test.exe
	@echo "--- FOC handoff gate (hosted) ---"; ./tests/foc_handoff_gate_test.exe
	@echo "--- ADC frame dual (hosted) ---"; ./tests/adc_frame_host_test.exe
	@echo "--- Current reconstruct (hosted) ---"; ./tests/current_reconstruct_test.exe
	@echo "--- PWM HS-1 replacement (hosted) ---"; ./tests/pwm_hs1_test.exe
	@echo "--- PWM break init regression (hosted) ---"; ./tests/pwm_break_init_test.exe
	@echo "--- FOC start fail-closed / success gates (hosted) ---"; ./tests/foc_start_gate_test.exe
	@echo "--- PWM HS-1 default-deny compile ---"; $(MAKE) -s pwm_hs1_default_deny

	@echo "--- Frame-aware protection (hosted) ---"; ./tests/protect_frame_host_test.exe
	@echo "--- Measured map selector (hosted) ---"; ./tests/current_map_selector_test.exe
	@echo "--- Map capture service path (hosted) ---"; ./tests/map_capture_test.exe
	@echo "--- Map capture port boundary (hosted) ---"; ./tests/map_capture_port_test.exe
	@echo "--- SD direct interlock (hosted) ---"; ./tests/sd_interlock_test.exe
	@echo "--- SD direct latch/clear (hosted) ---"; ./tests/sd_latch_test.exe
	@echo "--- SD direct no-self-rearm (hosted) ---"; ./tests/sd_no_self_rearm_test.exe
	@echo "--- Map builder (hosted) ---"; ./tests/map_builder_test.exe

test-qemu: tests/foc_test_qemu.elf tests/vf_test_qemu.elf
	@set -eu; \
	log=$$(mktemp); \
	trap 'rm -f "$$log"' EXIT INT TERM; \
	echo "--- FOC math (QEMU) ---"; \
	$(QEMU) -M olimex-stm32-h405 -nographic -semihosting-config enable=on,target=native -kernel tests/foc_test_qemu.elf >"$$log" 2>&1 || { cat "$$log"; echo "QEMU FOC test failed" >&2; exit 1; }; \
	cat "$$log"; \
	grep -q 'ALL PASS' "$$log" || { echo "QEMU FOC test did not report ALL PASS" >&2; exit 1; }; \
	echo "--- V/f control (QEMU) ---"; \
	$(QEMU) -M olimex-stm32-h405 -nographic -semihosting-config enable=on,target=native -kernel tests/vf_test_qemu.elf >"$$log" 2>&1 || { cat "$$log"; echo "QEMU V/f test failed" >&2; exit 1; }; \
	cat "$$log"; \
	grep -q 'ALL PASS' "$$log" || { echo "QEMU V/f test did not report ALL PASS" >&2; exit 1; }

tests/foc_test_hosted.exe: tests/foc_math_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/foc_math_test.c $(TEST_COMMON) tests/mocks/vfc_stub.c -lm -o $@

tests/vf_test_hosted.exe: tests/vf_control_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/vf_control_test.c tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/foc_handoff_gate.c src/foc_run_policy.c src/foc_slip_policy.c src/current_reconstruct.c src/current_map_selector.c src/vf_control.c -o $@

tests/cordic_mod_test.exe: tests/cordic_mod_test.c
	$(HOSTED_GCC) $(MOCK_INC) -I src tests/cordic_mod_test.c -o $@

tests/vm_test_hosted.exe: tests/vm_test.c tests/mocks/mock_cordic.c src/voltage_manager.c src/voltage_manager.h
	$(HOSTED_GCC) -I src $(MOCK_INC) tests/vm_test.c tests/mocks/mock_cordic.c src/voltage_manager.c -o $@

# Ревью TEST-03: portabled ISR-решения и handoff-gate (не требуют STM32)
tests/control_isr_test.exe: tests/control_isr_test.c src/control_isr.c src/control_isr.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/control_isr.c tests/control_isr_test.c -o $@

tests/foc_handoff_gate_test.exe: tests/foc_handoff_gate_test.c src/foc_handoff_gate.c src/foc_handoff_gate.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/foc_handoff_gate.c tests/foc_handoff_gate_test.c -o $@

tests/foc_run_policy_test.exe: tests/foc_run_policy_test.c src/foc_run_policy.c src/foc_run_policy.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/foc_run_policy.c tests/foc_run_policy_test.c -o $@

tests/foc_slip_policy_test.exe: tests/foc_slip_policy_test.c src/foc_slip_policy.c src/foc_slip_policy.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/foc_slip_policy.c tests/foc_slip_policy_test.c -o $@

# Ревью ADC-2S: dual injected simultaneous AdcFrame (мок регистров в mocks_adc)
tests/adc_frame_host_test.exe: tests/adc_frame_host_test.c src/adc.c src/adc.h tests/mocks_adc/stm32g474xx.h tests/mocks_adc/registers.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc -Itests/mocks_adc src/adc.c tests/adc_frame_host_test.c tests/mocks_adc/registers.c -o $@

tests/pwm_hs1_test.exe: tests/pwm_hs1_test.c src/pwm.c src/pwm.h src/pwm_board_pins.c src/pwm_board_pins.h tests/hs1_mock/stm32g474xx.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -DOEW_HS1_COMMISSIONING_RELEASE=1 -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/hs1_mock -Isrc src/pwm.c src/pwm_board_pins.c tests/pwm_hs1_test.c -o $@

tests/pwm_break_init_test.exe: tests/pwm_break_init_test.c src/pwm.c src/pwm.h src/pwm_board_pins.c src/pwm_board_pins.h tests/hs1_mock/stm32g474xx.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/hs1_mock -Isrc src/pwm.c src/pwm_board_pins.c tests/pwm_break_init_test.c -o $@

tests/foc_start_gate_test.exe: tests/foc_start_gate_test.c tests/foc_start_gate_mocks.c src/foc.c src/foc_handoff_gate.c src/foc_run_policy.c src/foc_slip_policy.c src/current_map_selector.c src/current_reconstruct.c src/pwm.c src/pwm_board_pins.c tests/hs1_mock/stm32g474xx.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -DOEW_HS1_COMMISSIONING_RELEASE=1 -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/hs1_mock -Isrc src/foc.c src/foc_handoff_gate.c src/foc_run_policy.c src/foc_slip_policy.c src/current_map_selector.c src/current_reconstruct.c src/pwm.c src/pwm_board_pins.c tests/mocks/mock_cordic.c tests/foc_start_gate_mocks.c tests/foc_start_gate_test.c -o $@



# OEW-HS-1 default-deny: без OEW_HS1_COMMISSIONING_RELEASE=1 компиляция обязана
# проходить, а PWM_HardwareInterlockHealthy() — возвращать false.
pwm_hs1_default_deny: src/pwm.c src/pwm.h tests/hs1_mock/stm32g474xx.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/hs1_mock -Isrc -c src/pwm.c -o $@.o
	rm -f $@.o

tests/protect_frame_host_test.exe: tests/protect_frame_host_test.c src/protect.c src/protect.h tests/hs1_mock/stm32g474xx.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -Itests/hs1_mock -Isrc src/protect.c tests/protect_frame_host_test.c -o $@

tests/current_map_selector_test.exe: tests/current_map_selector_test.c src/current_map_selector.c src/current_map_selector.h src/current_reconstruct.c src/current_reconstruct.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/current_map_selector.c src/current_reconstruct.c tests/current_map_selector_test.c -o $@

tests/map_capture_test.exe: tests/map_capture_test.c src/map_capture.c src/map_capture.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/map_capture.c tests/map_capture_test.c -o $@

tests/map_capture_port_test.exe: tests/map_capture_port_test.c src/map_capture.c src/map_capture.h src/map_capture_port.c src/map_capture_port.h tests/mapcap_mock/adc.h tests/hs1_mock/stm32g474xx.h
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/mapcap_mock -Itests/hs1_mock -Isrc src/map_capture.c src/map_capture_port.c tests/map_capture_port_test.c -o $@


tests/sd_interlock_test.exe: tests/sd_interlock_test.c src/pwm.c src/pwm_board_pins.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -DOEW_HS1_COMMISSIONING_RELEASE=1 -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/hs1_mock -Isrc src/pwm.c src/pwm_board_pins.c tests/sd_interlock_test.c -o $@

tests/sd_latch_test.exe: tests/sd_latch_test.c src/pwm.c src/pwm_board_pins.c src/protect.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -DOEW_HS1_COMMISSIONING_RELEASE=1 -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/hs1_mock -Isrc src/pwm.c src/pwm_board_pins.c src/protect.c tests/sd_latch_test.c -o $@

tests/sd_no_self_rearm_test.exe: tests/sd_no_self_rearm_test.c src/pwm.c src/pwm_board_pins.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -DPWM_HOST_TEST -DOEW_HS1_COMMISSIONING_RELEASE=1 -DPWM_OEW_ADC_TRIGGER_REVISION=0x4F455731u -Itests/hs1_mock -Isrc src/pwm.c src/pwm_board_pins.c tests/sd_no_self_rearm_test.c -o $@

tests/map_builder_test.exe: tests/map_builder_test.c src/map_builder.c src/map_builder.h src/current_map_selector.c src/current_reconstruct.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/map_builder.c src/current_map_selector.c src/current_reconstruct.c tests/map_builder_test.c -o $@

tests/foc_test_qemu.elf: tests/foc_math_test.c tests/qemu_startup.s tests/qemu_test.ld
	$(ARM_GCC) -mcpu=cortex-m4 -mthumb -mfloat-abi=soft $(MOCK_INC) -I src -ffunction-sections -fdata-sections tests/qemu_startup.s tests/foc_math_test.c $(TEST_COMMON) tests/mocks/vfc_stub.c -Wl,--gc-sections -T tests/qemu_test.ld -nostdlib -lgcc -o $@

tests/vf_test_qemu.elf: tests/vf_control_test.c tests/qemu_startup.s tests/qemu_test.ld
	$(ARM_GCC) -mcpu=cortex-m4 -mthumb -mfloat-abi=soft $(MOCK_INC) -I src -ffunction-sections -fdata-sections tests/qemu_startup.s tests/vf_control_test.c tests/mocks/mock_cordic.c tests/mocks/foc_stubs.c src/foc.c src/foc_handoff_gate.c src/foc_run_policy.c src/foc_slip_policy.c src/current_reconstruct.c src/current_map_selector.c src/vf_control.c -Wl,--gc-sections -T tests/qemu_test.ld -nostdlib -lgcc -o $@

tests/current_reconstruct_test.exe: tests/current_reconstruct_test.c src/current_reconstruct.c src/current_reconstruct.h src/adc.h tests/adc_frame_stub.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/current_reconstruct.c tests/current_reconstruct_test.c tests/adc_frame_stub.c -o $@
