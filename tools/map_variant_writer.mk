# Hosted-цели генератора вариантов карты (TZ-02 experimental M-tuning).
# Автономный mk: НЕ включается в firmware C_SOURCES и не меняет корневой Makefile.
HOSTED_GCC ?= gcc
VARIANT_TEST := tests/map_variant_writer_test.exe
VARIANT_CLI := tools/map_variant_cli.exe

VARIANT_SRCS = \
 tools/map_variant_writer.c tools/map_variant_writer.h \
 tools/map_artifact_writer.c tools/map_artifact_writer.h \
 src/map_artifact_decoder.c src/map_artifact_decoder.h \
 src/current_map_selector.c src/current_map_selector.h \
 src/current_reconstruct.c src/current_reconstruct.h \
 tests/adc_frame_stub.c

.PHONY: map-variant-test
map-variant-test: $(VARIANT_TEST)
	@echo "--- Map variant writer (hosted) ---"
	./$(VARIANT_TEST)

$(VARIANT_TEST): tests/map_variant_writer_test.c $(VARIANT_SRCS)
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Itools -Isrc \
		tests/map_variant_writer_test.c \
		tools/map_variant_writer.c \
		tools/map_artifact_writer.c \
		src/map_artifact_decoder.c \
		src/current_map_selector.c \
		src/current_reconstruct.c \
		tests/adc_frame_stub.c \
		-lm -o $@

.PHONY: map-variant-cli
map-variant-cli: $(VARIANT_CLI)

$(VARIANT_CLI): tools/map_variant_cli.c $(VARIANT_SRCS)
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Itools -Isrc \
		tools/map_variant_cli.c \
		tools/map_variant_writer.c \
		tools/map_artifact_writer.c \
		src/map_artifact_decoder.c \
		src/current_map_selector.c \
		src/current_reconstruct.c \
		tests/adc_frame_stub.c \
		-lm -o $@

# e2e: фикстура → M1/M2/M3 → проверки размера, различий и негативных кейсов.
# Логика вынесена в sh-скрипт (переносимость git-bash/MSYS, как у run_map_artifact_writer_test.sh).
.PHONY: map-variant-e2e
map-variant-e2e: $(VARIANT_CLI)
	@sh tools/run_map_variant_writer_test.sh
