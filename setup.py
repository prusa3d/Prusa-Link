"""Setup.py for PrusaLink software."""
import logging
import os
import re
from grp import getgrnam
from shutil import copyfile, copytree
from subprocess import run
from sys import stderr
from typing import ClassVar

from setuptools import Command, find_namespace_packages, setup  # type: ignore

from prusa.link import __author_email__, __author_name__, __version__
from prusa.link import __doc__ as description  # type: ignore

RPI_MODEL_PATH = "/sys/firmware/devicetree/base/model"
RE_GIT = re.compile(r'(-e )?git\+|:')
RE_EGG = re.compile(r'#egg=(.*)$')
REQUIRES = []


def fill_requires(filename):
    """Fill REQUIRES lists."""
    with open(filename, "r", encoding='utf-8') as requirements:
        for line in requirements:
            line = line.strip()
            if RE_GIT.match(line):
                match = RE_EGG.search(line)
                if match:
                    REQUIRES.append(f"{match.groups()[0]} @ {line}")
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
        with open(RPI_MODEL_PATH, encoding='utf-8') as model_file:
            if "Pi" in model_file.read():
                fill_requires("requirements-pi.txt")
except Exception:  # pylint: disable=broad-except
    print("This is not a Raspberry Pi -> wiringpi installation won't be "
          "attempted!")


def doc():
    """Return README.md content."""
    with open('README.md', 'r', encoding='utf-8') as readme:
        return readme.read().strip()


class BuildStatic(Command):
    """Build static html files, need docker."""
    description = __doc__
    user_options: ClassVar[list[str]] = [
            ('target-dir=', 't',
             "target build directory (default: './prusa/link/static')"),
            ]
    target_dir = None

    def initialize_options(self):
        self.target_dir = None

    def finalize_options(self):
        if self.target_dir is None:
            cwd = os.path.abspath(os.curdir)
            self.target_dir = os.path.join(cwd, 'prusa', 'link', 'static')

    def run(self):
        logging.info("building html documentation")
        if self.dry_run:
            if run(['docker', 'version'], check=False).returncode:
                raise IOError(1, 'docker failed')
            return

        git_ret = run(['git', 'rev-parse', '--short', 'HEAD'],
                      check=False, capture_output=True)
        if git_ret.returncode:
            raise IOError(1, "Can't get git commit hash.")
        git_commit_hash = git_ret.stdout.strip()

        if run(['docker', 'pull', 'node:latest'], check=False).returncode:
            raise IOError(1, "Can't get last node docker.")

        cwd = os.path.abspath(os.path.join(os.curdir, 'Prusa-Link-Web'))

        copyfile(os.path.join(os.curdir, 'config.custom.js'),
                 os.path.join(cwd, 'config.custom.js'))

        args = ('docker', 'run', '-t', '--rm', '-u',
                f"{os.getuid()}:{getgrnam('docker').gr_gid}", '-w', cwd,
                '-v', f"{cwd}:{cwd}",
                'node:latest', 'sh', '-c',
                'npm install && npm run words:extract')
        if run(args, check=False).returncode:
            raise IOError(1, 'docker failed')

        args = ('docker', 'run', '-t', '--rm', '-u',
                f"{os.getuid()}:{getgrnam('docker').gr_gid}", '-w', cwd,
                '-v', f"{cwd}:{cwd}",
                '-e', f'GIT_COMMIT_HASH={git_commit_hash}',
                'node:latest', 'sh', '-c',
                'npm run build:custom')
        if run(args, check=False).returncode:
            raise IOError(1, 'docker failed')

        # pylint: disable=unexpected-keyword-arg
        # (python 3.7)
        copytree(os.path.join(cwd, 'dist'),
                 os.path.join(self.target_dir),
                 dirs_exist_ok=True)


setup(
    name="prusalink",
    version=__version__,
    description=description.split("\n", maxsplit=1)[0],
    author=__author_name__,
    author_email=__author_email__,
    maintainer=__author_name__,
    maintainer_email=__author_email__,
    license="Freeware",
    url="https://github.com/prusa3d/Prusa-Link",
    packages=find_namespace_packages(include=['prusa.*']),
    include_package_data=True,
    data_files=[('share/prusalink',
                 ['README.md', 'ChangeLog', 'CONTRIBUTION.md'])],
    scripts=['prusalink-boot'],
    long_description=doc(),
    long_description_content_type="text/markdown",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Natural Language :: English",
        "License :: Freeware",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
    ],
    python_requires=">=3.9",
    install_requires=REQUIRES,
    entry_points={'console_scripts': [
        'prusalink = prusa.link.__main__:main',
        'prusalink-manager = prusa.link.multi_instance.__main__:main',
    ]},
    cmdclass={'build_static': BuildStatic})
