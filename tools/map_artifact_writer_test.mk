# Included/standalone host target for the v2 map artifact writer.
# This file intentionally does not alter firmware C_SOURCES.
HOSTED_GCC ?= gcc
MAP_ARTIFACT_TEST := tests/map_artifact_writer_test.exe

.PHONY: map-artifact-test
map-artifact-test: $(MAP_ARTIFACT_TEST)
	@echo "--- Map artifact writer v2 (hosted) ---"
	./$(MAP_ARTIFACT_TEST)

$(MAP_ARTIFACT_TEST): tests/map_artifact_writer_test.c \
 tools/map_artifact_writer.c tools/map_artifact_writer.h \
 src/current_map_selector.c src/current_map_selector.h \
 src/current_reconstruct.c src/current_reconstruct.h \
 tests/adc_frame_stub.c
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror \
		-Itools -Isrc \
		tests/map_artifact_writer_test.c \
		tools/map_artifact_writer.c \
		src/current_map_selector.c \
		src/current_reconstruct.c \
		tests/adc_frame_stub.c \
		-lm -o $@
