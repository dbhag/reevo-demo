PYTHON := python3
EXPORT_DIR := data/generated
CONFIG := fixtures/reevo_pipeline.yaml
MANIFEST := $(EXPORT_DIR)/manifest.csv
PYTHON_MIN_MAJOR := 3
PYTHON_MIN_MINOR := 10

.PHONY: demo clean check-python publish

demo: check-python
	$(PYTHON) generate_dataset.py --seed 42 --n 4000 --out-dir $(EXPORT_DIR)
	$(PYTHON) audit.py --export-dir $(EXPORT_DIR) --config $(CONFIG) --out report.md --manifest $(MANIFEST)
	$(PYTHON) generate_dashboard.py --export-dir $(EXPORT_DIR) --config $(CONFIG) --manifest $(MANIFEST) --out dashboard.html
	@echo "Report:    report.md"
	@echo "Dashboard: dashboard.html"

check-python:
	@$(PYTHON) -c "import sys; assert (sys.version_info.major, sys.version_info.minor) >= ($(PYTHON_MIN_MAJOR), $(PYTHON_MIN_MINOR)), f'Need Python >= $(PYTHON_MIN_MAJOR).$(PYTHON_MIN_MINOR), found {sys.version.split()[0]}'"

publish: demo
	mkdir -p docs
	cp dashboard.html docs/index.html
	@echo "Published: docs/index.html"

clean:
	rm -rf $(EXPORT_DIR) report.md dashboard.html
