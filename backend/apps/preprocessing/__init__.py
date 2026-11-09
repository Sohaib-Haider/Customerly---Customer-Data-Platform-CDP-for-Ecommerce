"""Preprocessing app for the CDP backend.

Provides basic data cleaning functions for user-uploaded CSVs:
missing values, duplicates, column names, data types, dates,
whitespace, outliers, categorical encoding, and numeric normalization.

Column-to-schema mapping, ID namespacing, and store_id tagging are
handled by the LLM mapping service (separate concern).
"""
