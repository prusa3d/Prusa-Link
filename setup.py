"""Setup.py for Prusa-Link software."""
import os
import re

from sys import stderr
from setuptools import setup, find_namespace_packages

from prusa.link import __version__, __doc__

RPI_MODEL_PATH = "/sys/firmware/devicetree/base/model"
RE_GIT = re.compile(r'(-e )?git\+|:')
RE_EGG = re.compile(r'#egg=(.*)$')
REQUIRES = []


def fill_requires(filename):
    """Fill REQUIRES lists."""
    with open(filename, "r") as requirements:
        for line in requirements:
            line = line.strip()
            if RE_GIT.match(line):
                match = RE_EGG.search(line)
                if match:
                    REQUIRES.append("%s @ %s" % (match.groups()[0], line))
                else:
                    print(
                        'Dependency to a git repository must have the format:',
                        file=stderr)
                    print(
                        '\tgit+ssh://git@github.com/xxx/xxx#egg=package_name',
                        file=stderr)
            else:
                REQUIRES.append(line)


fill_requires("requirements.txt")
try:
    if os.path.exists(RPI_MODEL_PATH):
        with open(RPI_MODEL_PATH) as model_file:
            if "Pi" in model_file.read():
                fill_requires("requirements-pi.txt")
except Exception:  # pylint: disable=broad-except
    print("This is not a Raspberry Pi -> wiringpi installation won't be "
          "attempted!")


def doc():
    """Return README.md content."""
    with open('README.md', 'r') as readme:
        return readme.read().strip()


def find_data_files(directory, target_folder=""):
    """Find files in directory, and prepare tuple for setup."""
    rv = []      # pylint: disable=C0103
    for root, dirs, files in os.walk(directory):   # pylint: disable=W0612
        if target_folder:
            rv.append((target_folder+root[len(directory):],
                       list(root+'/'+f
                            for f in files if f[0] != '.' and f[-1] != '~')))
        else:
            rv.append((root,
                       list(root+'/'+f
                            for f in files if f[0] != '.' and f[-1] != '~')))
    return rv


setup(
    name="prusa-link",
    version=__version__,
    description=__doc__,
    author="Tomáš Jozífek",
    author_email="tomas.jozifek@prusa3d.cz",
    maintainer="Tomáš Jozífek",
    maintainer_email="tomas.jozifek@prusa3d.cz",
    url="https://github.com/prusa3d/Prusa-Link",
    packages=find_namespace_packages(include=['prusa.*']),
    include_package_data=True,
    data_files=[('share/prusa-link',
                 ['README.md', 'ChangeLog', 'CONTRIBUTION.md'])],
    long_description=doc(),
    long_description_content_type="text/markdown",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Natural Language :: English",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
    ],
    python_requires=">=3.7",
    install_requires=REQUIRES,
    entry_points={
        'console_scripts': [
            'prusa-link = prusa.link.__main__:main'
        ]
    }
)
