from setuptools import setup, find_packages

from prusa_link import __version__, __doc__


REQUIRES = []
with open("requirements.txt", "r") as requires:
    for line in requires:
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
