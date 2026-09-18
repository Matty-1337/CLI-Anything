#!/usr/bin/env python3
from pathlib import Path
from setuptools import find_namespace_packages, setup

HERE = Path(__file__).parent

setup(
    name="cli-anything-davinci-resolve",
    version="1.0.0",
    description="Agent-native CLI for a locally running DaVinci Resolve Studio session",
    long_description=(HERE / "cli_anything/davinci_resolve/README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    url="https://github.com/HKUDS/CLI-Anything",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.10",
    install_requires=["click>=8.0.0", "prompt-toolkit>=3.0.0"],
    extras_require={"dev": ["pytest>=7.0.0", "pytest-cov>=4.0.0"]},
    entry_points={
        "console_scripts": [
            "cli-anything-davinci-resolve=cli_anything.davinci_resolve.davinci_resolve_cli:main",
            "davinci=cli_anything.davinci_resolve.davinci_resolve_cli:main",
        ]
    },
    package_data={"cli_anything.davinci_resolve": ["skills/*.md"]},
    include_package_data=True,
    zip_safe=False,
)
