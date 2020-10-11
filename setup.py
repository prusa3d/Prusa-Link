import os

from setuptools import setup, find_packages

from prusa_link import __version__, __doc__

RPI_MODEL_PATH = "/sys/firmware/devicetree/base/model"

is_raspberry = False

try:
    if os.path.exists(RPI_MODEL_PATH):
        with open(RPI_MODEL_PATH) as model_file:
            model = model_file.read()
            if "Pi" in model:
                is_raspberry = True
except:
    print("This is not a Raspberry Pi -> wiringpi installation won't be "
          "attempted!")

if is_raspberry:
    requirements_path = "requirements-pi.txt"
else:
    requirements_path = "requirements.txt"


REQUIRES = []
with open(requirements_path, "r") as requirements_path:
    for line in requirements_path:
        REQUIRES.append(line.strip())


def doc():
    """Return README.md content."""
    with open('README.md', 'r') as readme:
        return readme.read().strip()


setup(
    name="prusa-link",
    version=__version__,
    description=__doc__,
    author="Tomáš Jozífek",
    author_email="tomas.jozifek@prusa3d.cz",
    maintainer="Tomáš Jozífek",
    maintainer_email="tomas.jozifek@prusa3d.cz",
    url="https://github.com/prusa3d/Prusa-Link",
    packages=find_packages(),
    package_data={'installation.data_files':
                     ['prusa-link.service']},
    long_description=doc(),
    long_description_content_type="text/markdown",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Natural Language :: English",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
    ],
    python_requires=">=3.7",
    install_requires=REQUIRES,
    entry_points={
        'console_scripts': [
            'prusa_link = prusa_link.__main__:main',
            'prusa_link_install = installation.__main__:main',
        ]
    }
)
