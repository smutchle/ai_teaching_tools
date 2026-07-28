"""Core library for the AI Podcast Generator.

Pipeline: PDF or transcript -> LLM script -> ElevenLabs speech -> zimage cover art
-> ZIP bundle. Each stage is an independent module with typed inputs.
"""
