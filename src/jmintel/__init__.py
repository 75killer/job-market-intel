"""jmintel —— 招聘与公司情报分析工具包。

项目名释义：jm = job market，intel = intelligence（情报）。
包结构约定（这是 Python 项目的通用做法，面试可以讲）：

    jmintel/
        config.py        全局配置：路径、常量、参数
        logging_setup.py 日志：让程序运行过程可追溯
        http.py          网络请求：统一超时、重试、限速
        storage.py       存储：原始数据落盘 + 写 SQLite
        sources/         数据源适配器（每接一个新网站就加一个文件）
        cleaning.py      数据清洗：把脏数据变成规范表
        features.py      特征工程
        analysis.py      探索性分析
        modeling.py      机器学习建模

这样分层的好处：
1. 加新数据源不用改动已有代码（开闭原则，面试可以提这个词）
2. 每层可以单独测试
3. 出了 bug 能快速定位在哪一层
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
