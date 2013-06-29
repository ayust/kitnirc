#!/usr/bin/python
from distutils.core import setup

from kitnirc import __version__

setup(
    name="KitnIRC",
    version=__version__,
    description="IRC bot framework",
    license="MIT License",
    author="Amber Yust",
    author_email="amber.yust@gmail.com",
    url="https://github.com/ayust/kitnirc",
    download_url="https://github.com/ayust/kitnirc/downloads",
    provides=[
        "kitnirc",
    ],
    packages=[
        "kitnirc",
        "kitnirc.contrib",
    ],
    data_files=[
        ('', ["LICENSE", "README.md"]),
    ],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: MIT License",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 2.7",
        "Topic :: Communications :: Chat :: Internet Relay Chat",
    ],
)

# vim: set ts=4 sts=4 sw=4 et:
