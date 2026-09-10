from setuptools import setup, find_packages
import os

setup(
    name="jarvis",
    version="2.0.0",
    description="J.A.R.V.I.S. — Tactical Personal & OSINT Assistant",
    py_modules=["jarvis"],          
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "textual>=0.47.0",
        "httpx>=0.26.0",
        "python-whois>=0.9.4",
        "dnspython>=2.6.0",
        "pywebview>=5.0.0",
        "rich>=13.7.0",
    ],
    entry_points={
        "console_scripts": [
            "jarvis=jarvis:main",
        ],
    },
)