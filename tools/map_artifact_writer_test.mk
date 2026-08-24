# Included/standalone host targets for the v2 map artifact writer and the
# L3 measurement pipeline (Accumulator -> Solver -> Certifier -> Writer).
# This file intentionally does not alter firmware C_SOURCES.
HOSTED_GCC ?= gcc
MAP_ARTIFACT_TEST := tests/map_artifact_writer_test.exe
MAP_ARTIFACT_PIPELINE_TEST := tests/map_artifact_pipeline_test.exe
MAP_ARTIFACT_CLI := tools/map_artifact_pipeline_cli.exe

MAP_PIPELINE_SRCS = \
 tools/map_artifact_pipeline.c tools/map_artifact_pipeline.h \
 tools/map_artifact_writer.c tools/map_artifact_writer.h \
 src/current_map_selector.c src/current_map_selector.h \
 src/current_reconstruct.c src/current_reconstruct.h \
 src/map_measurement_accumulator.c src/map_measurement_accumulator.h \
 src/map_measurement_reference.c src/map_measurement_reference.h \
 src/map_measurement_solver.c src/map_measurement_solver.h \
 src/map_region_certifier.c src/map_region_certifier.h \
 tests/adc_frame_stub.c

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

.PHONY: map-artifact-pipeline-test
map-artifact-pipeline-test: $(MAP_ARTIFACT_PIPELINE_TEST)
	@echo "--- Map artifact pipeline (hosted) ---"
	./$(MAP_ARTIFACT_PIPELINE_TEST)

$(MAP_ARTIFACT_PIPELINE_TEST): tests/map_artifact_pipeline_test.c \
 $(MAP_PIPELINE_SRCS)
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror \
		-Itools -Isrc \
		tests/map_artifact_pipeline_test.c \
		tools/map_artifact_pipeline.c \
		tools/map_artifact_writer.c \
		src/current_map_selector.c \
		src/current_reconstruct.c \
		src/map_measurement_accumulator.c \
		src/map_measurement_reference.c \
		src/map_measurement_solver.c \
		src/map_region_certifier.c \
		tests/adc_frame_stub.c \
		-lm -o $@

.PHONY: map-artifact-cli
map-artifact-cli: $(MAP_ARTIFACT_CLI)

$(MAP_ARTIFACT_CLI): tools/map_artifact_pipeline_cli.c \
 $(MAP_PIPELINE_SRCS)
	$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror \
		-Itools -Isrc \
		tools/map_artifact_pipeline_cli.c \
		tools/map_artifact_pipeline.c \
		tools/map_artifact_writer.c \
		src/current_map_selector.c \
		src/current_reconstruct.c \
		src/map_measurement_accumulator.c \
		src/map_measurement_reference.c \
		src/map_measurement_solver.c \
		src/map_region_certifier.c \
		tests/adc_frame_stub.c \
		-lm -o $@

# End-to-end: run the CLI on the demo dataset and verify the 497-byte
# artifact is produced next to the audit JSON.
.PHONY: map-artifact-e2e
map-artifact-e2e: $(MAP_ARTIFACT_CLI)
	@rm -rf e2e_out
	@mkdir -p e2e_out
	./$(MAP_ARTIFACT_CLI) tools/map_artifact_pipeline_demo.txt e2e_out
	@test -s e2e_out/oew_map_v2.bin
	@test "$$(wc -c < e2e_out/oew_map_v2.bin)" = "497"
	@test -s e2e_out/oew_map_v2.json
	@echo "--- Map artifact pipeline e2e: PASS (497-byte artifact + audit JSON) ---"
	@rm -rf e2e_out
