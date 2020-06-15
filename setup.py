from setuptools import setup, find_packages

from old_buddy import __version__, __doc__


REQUIRES = []
with open("requirements.txt", "r") as requires:
    for line in requires:
        REQUIRES.append(line.strip())


def doc():
    """Return README.md content."""
    with open('README.md', 'r') as readme:
        return readme.read().strip()


setup(
    name="Old Buddy",
    version=__version__,
    description=__doc__,
    author="Tomáš Jozífek",
    author_email="tomas.jozifek@prusa3d.cz",
    maintainer="Tomáš Jozífek",
    maintainer_email="tomas.jozifek@prusa3d.cz",
    url="https://github.com/prusa3d/Prusa-Connect-MK3",
    packages=find_packages(),
    package_data={'installation.data_files':
                     ['old_buddy.service']},
    long_description=doc(),
    long_description_content_type="text/markdown",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Natural Language :: English",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
    ],
    python_requires=">=3",
    install_requires=REQUIRES,
    entry_points={
        'console_scripts': [
            'old_buddy = old_buddy.__main__:main',
            'old_buddy_install = installation.__main__:main',
        ]
    }
)
