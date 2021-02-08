.PHONY: clean requirements.txt requirements-dev.txt

requirements.txt:
	poetry export -f requirements.txt > requirements.txt

requirements-dev.txt:
	poetry export -f requirements.txt --dev > requirements-dev.txt
