"""量化交易系统安装配置"""

from setuptools import setup, find_packages

setup(
    name="quant_trading",
    version="0.1.0",
    description="多市场量化交易系统 - 支持 OKX 加密货币、IB 美股、CTP 期货",
    author="",
    python_requires=">=3.12",
    packages=find_packages(),
    install_requires=[
        "python-okx>=0.3.0",
        "websockets>=12.0",
        "PyYAML>=6.0",
        "aiohttp>=3.9.0",
        "requests>=2.31.0",
    ],
    extras_require={
        "test": ["pytest>=8.0.0", "pytest-asyncio>=0.23.0"],
        "ib": ["ib_insync>=0.9.86"],
        "ctp": ["vnpy_ctp>=3.7.0"],
    },
)
