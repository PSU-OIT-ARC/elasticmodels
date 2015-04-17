# run the tests for python2 and python3
test:
	@#@$(MAKE) test2
	@$(MAKE) test3

# run tests for python 2
test2: .env2
	.env2/bin/python runtests.py

# run tests for python 3
test3: .env3
	.env3/bin/python runtests.py

# remove junk
clean:
	rm -rf .env2 .env3
	find -iname "*.pyc" -or -iname "__pycache__" -delete

# setup a virtualenv for python2
.env2:
	virtualenv --no-site-packages -p python .env2
	.env2/bin/pip install -e .[test]

# setup a virtualenv for python3 and install pip
.env3:
	python3 -m venv .env3
	curl https://raw.githubusercontent.com/pypa/pip/master/contrib/get-pip.py | .env3/bin/python
	.env3/bin/pip install -e .[test]

coverage: .env3
	.env3/bin/coverage run runtests.py
	coverage html --omit ".env*"
