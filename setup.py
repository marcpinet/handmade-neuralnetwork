from setuptools import setup, find_packages

setup(
    name='neuralnetlib',
    version='4.3.3',
    author='Marc Pinet',
    description='A flexible deep learning framework built from scratch using only NumPy',
    long_description=open('README.md', encoding="utf-8").read(),
    long_description_content_type='text/markdown',
    url='https://github.com/marcpinet/neuralnetlib',
    packages=find_packages(),
    install_requires=[
        'numpy',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Education',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
    ],
    python_requires='>=3.8',
)
