.SUFFIXES:
MAKEFLAGS += --no-builtin-rules
.ONESHELL:

version := $(shell python -m setuptools_scm)
$(info version: ${version})

pyz: dist/reinkpy-${version}.pyz
.DEFAULT_GOAL := pyz

deps := lib/asciimatics pyusb pysnmp-lextudio zeroconf
wdir := build/wheels
fdir := build/files

dist/reinkpy-${version}.pyz:
	mkdir -p ${@D} ${wdir} ${fdir}
	python -m pip wheel --wheel-dir ${wdir} --find-links ${wdir} . ${deps}
	rm -f ${wdir}/*.zip ; for f in ${wdir}/*.whl ; do ln -frs $$f $$f.zip ; done
	unzip "${wdir}/*.zip" -d ${fdir} # rm -rf ${fdir}/*.dist-info
	python -m zipapp -p "/usr/bin/env python3" --main "reinkpy.ui:main" -c --output $@ ${fdir}

clean::
	rm -rf build
