#!/usr/bin/env python3
# -*- coding: utf-8 -*-


"""
@File    : sticker_style.py
@Author  : zanne
@Date    : 2026/3/4 14:27
@Desc    : 
"""
from src.services.sticker import ThemeContentGenerator, PackGenerator

# from src.services.sticker import StyleAnalyzer
#
#
# sticerk_analyzer = StyleAnalyzer()
# sticerk_analyzer.analyze(image_path='gpt.png')

# Auto mode (one call does everything)
gen = PackGenerator()
# pack = gen.generate("人工智能", count=10)

# Interactive mode (review ThemeContent first)
theme_gen = ThemeContentGenerator()
content = theme_gen.generate("人工智能")
print(content.summary())  # review
pack = gen.generate("人工智能", theme_content=content,count=10)