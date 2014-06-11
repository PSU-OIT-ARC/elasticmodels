PYTHON=python

all: build

build:
	$(PYTHON) setup.py sdist

upload:
	$(PYTHON) setup.py sdist upload

clean:
	$(PYTHON) setup.py clean --all
	find . -name '*.py[co]' -exec rm -f "{}" ';'
	rm -rf build dist *.egg-info temp
