"""Setup.py for Prusa-Link software."""
import os
import re

from sys import stderr
from subprocess import call
from grp import getgrnam
from shutil import copytree
from distutils import log
from distutils.core import Command

from setuptools import setup, find_namespace_packages  # type: ignore

from prusa.link import __version__, __doc__ as description  # type: ignore

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
    rv = []  # pylint: disable=C0103
    for root, dirs, files in os.walk(directory):  # pylint: disable=W0612
        if target_folder:
            rv.append((target_folder + root[len(directory):],
                       list(root + '/' + f for f in files
                            if f[0] != '.' and f[-1] != '~')))
        else:
            rv.append((root,
                       list(root + '/' + f for f in files
                            if f[0] != '.' and f[-1] != '~')))
    return rv


class BuildStatic(Command):
    """Build static html files, need docker."""
    description = __doc__
    user_options = [('target-dir=', 't',
                     "target build directory (default: './prusa/link/static')")
                    ]
    target_dir = None

    def initialize_options(self):
        self.target_dir = None

    def finalize_options(self):
        if self.target_dir is None:
            cwd = os.path.abspath(os.curdir)
            self.target_dir = os.path.join(cwd, 'prusa', 'link', 'static')

    def run(self):
        log.info("building html documentation")
        if self.dry_run:
            if call(['docker', 'version']):
                raise IOError(1, 'docker failed')
            return

        if call(['docker', 'pull', 'node:latest']):
            raise IOError(1, "Can't get last node docker.")

        cwd = os.path.abspath(os.path.join(os.curdir, 'prusa-connect-local'))
        args = ('docker', 'run', '-t', '--rm', '-u',
                '%d:%d' % (os.getuid(), getgrnam('docker').gr_gid), '-w', cwd,
                '-v', '%s:%s' % (cwd, cwd), 'node:latest', 'sh', '-c',
                'npm install && npm run build:mk3')
        if call(args):
            raise IOError(1, 'docker failed')

        # pylint: disable=unexpected-keyword-arg
        # (python 3.7)
        copytree(os.path.join(cwd, 'dist'),
                 os.path.join(self.target_dir),
                 dirs_exist_ok=True)


setup(name="prusa-link",
      version=__version__,
      description=description,
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
          'console_scripts': ['prusa-link = prusa.link.__main__:main']
      },
      cmdclass={'build_static': BuildStatic})
