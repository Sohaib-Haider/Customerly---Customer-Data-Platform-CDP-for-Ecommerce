"""Unit tests for the preprocessing service."""

import os
import tempfile

import numpy as np
import pandas as pd
from django.test import TestCase

from apps.preprocessing.preprocessing import (
    clean_column_names,
    fix_data_types,
    handle_missing_values,
    handle_outliers,
    infer_column_types,
    parse_dates,
    remove_constant_columns,
    remove_duplicates,
    trim_whitespace,
    validate_required_columns,
)
from apps.preprocessing.pipeline import default_config, preprocess_csv, run_pipeline


class InferColumnTypesTest(TestCase):
    def test_numeric_detection(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [1.5, 2.5, 3.5]})
        types = infer_column_types(df)
        self.assertEqual(types["a"], "numeric")
        self.assertEqual(types["b"], "numeric")

    def test_categorical_detection(self):
        df = pd.DataFrame({"color": ["red", "blue", "red", "blue"]})
        types = infer_column_types(df)
        self.assertEqual(types["color"], "categorical")

    def test_text_detection(self):
        df = pd.DataFrame({"name": ["alice", "bob", "charlie", "diana"]})
        types = infer_column_types(df)
        self.assertEqual(types["name"], "text")

    def test_datetime_detection(self):
        df = pd.DataFrame({"date": ["2024-01-01", "2024-01-02", "2024-01-03"]})
        types = infer_column_types(df)
        self.assertEqual(types["date"], "datetime")


class CleanColumnNamesTest(TestCase):
    def test_normalization(self):
        df = pd.DataFrame({"First Name": [1], "Last-Name": [2], "Age  ": [3]})
        result = clean_column_names(df)
        self.assertIn("first_name", result.columns)
        self.assertIn("last_name", result.columns)
        self.assertIn("age", result.columns)

    def test_collision_handling(self):
        df = pd.DataFrame({"A B": [1], "a_b": [2]})
        result = clean_column_names(df)
        self.assertEqual(len(result.columns), 2)
        self.assertTrue(result.columns.is_unique)
        self.assertIn("a_b", result.columns)
        self.assertEqual(result.iloc[0].tolist(), [1, 2])


class TrimWhitespaceTest(TestCase):
    def test_trims_object_columns(self):
        df = pd.DataFrame({"name": ["  alice  ", " bob "], "city": ["  NY  ", "LA"]})
        result = trim_whitespace(df)
        self.assertEqual(result["name"].iloc[0], "alice")
        self.assertEqual(result["name"].iloc[1], "bob")
        self.assertEqual(result["city"].iloc[0], "NY")

    def test_specified_columns_only(self):
        df = pd.DataFrame({"name": ["  alice  "], "code": ["  X  "]})
        result = trim_whitespace(df, columns=["name"])
        self.assertEqual(result["name"].iloc[0], "alice")
        self.assertEqual(result["code"].iloc[0], "  X  ")


class ValidateRequiredColumnsTest(TestCase):
    def test_all_present(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        self.assertTrue(validate_required_columns(df, ["a", "b"]))

    def test_missing_raises(self):
        df = pd.DataFrame({"a": [1]})
        with self.assertRaises(ValueError):
            validate_required_columns(df, ["a", "b"], raise_on_missing=True)

    def test_missing_returns_false(self):
        df = pd.DataFrame({"a": [1]})
        self.assertFalse(validate_required_columns(df, ["a", "b"], raise_on_missing=False))


class RemoveConstantColumnsTest(TestCase):
    def test_removes_constant(self):
        df = pd.DataFrame({"a": [1, 1, 1], "b": [1, 2, 3]})
        result = remove_constant_columns(df)
        self.assertNotIn("a", result.columns)
        self.assertIn("b", result.columns)


class RemoveDuplicatesTest(TestCase):
    def test_removes_duplicates(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": [1, 1, 3]})
        result = remove_duplicates(df)
        self.assertEqual(len(result), 2)

    def test_subset(self):
        df = pd.DataFrame({"a": [1, 1, 2], "b": [1, 2, 3]})
        result = remove_duplicates(df, subset=["a"])
        self.assertEqual(len(result), 2)


class HandleMissingValuesTest(TestCase):
    def test_drop_strategy(self):
        df = pd.DataFrame({"a": [1, np.nan, 3], "b": [1, 2, 3]})
        result = handle_missing_values(df, strategy="drop")
        self.assertEqual(len(result), 2)

    def test_mean_strategy(self):
        df = pd.DataFrame({"a": [1.0, np.nan, 3.0]})
        result = handle_missing_values(df, strategy="mean")
        self.assertAlmostEqual(result["a"].iloc[1], 2.0)

    def test_mode_strategy(self):
        df = pd.DataFrame({"a": ["x", np.nan, "x", "y"]})
        result = handle_missing_values(df, strategy="mode")
        self.assertEqual(result["a"].iloc[1], "x")

    def test_type_aware_numeric(self):
        df = pd.DataFrame({"a": [1.0, np.nan, 3.0]})
        result = handle_missing_values(df, strategy="type_aware")
        self.assertFalse(result["a"].isna().any())


class FixDataTypesTest(TestCase):
    def test_numeric_cast(self):
        df = pd.DataFrame({"a": ["1", "2", "3"]})
        result = fix_data_types(df, type_map={"a": "int"})
        self.assertTrue(pd.api.types.is_integer_dtype(result["a"]))

    def test_float_cast(self):
        df = pd.DataFrame({"a": ["1.5", "2.5"]})
        result = fix_data_types(df, type_map={"a": "float"})
        self.assertTrue(pd.api.types.is_float_dtype(result["a"]))


class ParseDatesTest(TestCase):
    def test_date_parsing(self):
        df = pd.DataFrame({"date": ["2024-01-01", "2024-01-02"]})
        result = parse_dates(df, columns=["date"])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(result["date"]))


class HandleOutliersTest(TestCase):
    def test_cap_action(self):
        df = pd.DataFrame({"a": [1, 2, 3, 100]})
        result = handle_outliers(df, columns=["a"], method="iqr", action="cap")
        # Q1=1.75, Q3=27.25: the upper 1.5-IQR fence is 65.5.
        self.assertAlmostEqual(result["a"].max(), 65.5)
        self.assertEqual(result["a"].iloc[:3].tolist(), [1, 2, 3])
        self.assertEqual(len(result), len(df))

    def test_remove_action(self):
        df = pd.DataFrame({"a": [1, 2, 3, 100]})
        result = handle_outliers(df, columns=["a"], method="iqr", action="remove")
        self.assertEqual(len(result), 3)






class RunPipelineTest(TestCase):
    def test_preserves_numeric_units_and_category_labels(self):
        df = pd.DataFrame({
            "amount": [100000, 110000, 120000],
            "customer_group": ["Gold", "Silver", "Gold"],
        })
        result, _ = run_pipeline(df)
        self.assertEqual(result["amount"].tolist(), [100000, 110000, 120000])
        self.assertEqual(result["customer_group"].tolist(), ["Gold", "Silver", "Gold"])

    def test_basic_pipeline(self):
        df = pd.DataFrame(
            {
                "First Name": ["  alice  ", " bob ", None],
                "Age": [25, None, 35],
                "City": ["NY", "NY", "LA"],
            }
        )
        config = default_config()
        config["validate_required_columns"]["required_columns"] = []
        result, report = run_pipeline(df, config=config, generate_report=True)

        self.assertIsNotNone(report)
        self.assertEqual(report["rows_before"], 3)
        self.assertIn("clean_column_names", report["steps_executed"])
        self.assertIn("handle_missing_values", report["steps_executed"])

    def test_report_generation(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        result, report = run_pipeline(df, generate_report=True)
        self.assertIsInstance(report, dict)
        self.assertIn("per_step", report)


class PreprocessCSVTest(TestCase):
    def test_in_place_overwrite(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("a,b\n1,2\n3,4\n")
            tmp_path = f.name

        try:
            out_path, report = preprocess_csv(tmp_path, generate_report=True)
            self.assertEqual(out_path, tmp_path)
            df = pd.read_csv(tmp_path)
            self.assertEqual(len(df), 2)
        finally:
            os.unlink(tmp_path)
            for ext in [".bak", ".report.json"]:
                if os.path.exists(tmp_path + ext):
                    os.unlink(tmp_path + ext)

    def test_with_backup(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("a,b\n1,2\n3,4\n")
            tmp_path = f.name

        try:
            preprocess_csv(tmp_path, backup=True, generate_report=True)
            self.assertTrue(os.path.exists(tmp_path + ".bak"))
        finally:
            os.unlink(tmp_path)
            for ext in [".bak", ".report.json"]:
                if os.path.exists(tmp_path + ext):
                    os.unlink(tmp_path + ext)

    def test_custom_output_path(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("a,b\n1,2\n3,4\n")
            tmp_path = f.name

        out_path = tmp_path + ".out.csv"
        try:
            result, _ = preprocess_csv(tmp_path, output_path=out_path)
            self.assertEqual(result, out_path)
            df = pd.read_csv(out_path)
            self.assertEqual(len(df), 2)
        finally:
            os.unlink(tmp_path)
            if os.path.exists(out_path):
                os.unlink(out_path)
            if os.path.exists(out_path + ".report.json"):
                os.unlink(out_path + ".report.json")

    def test_chunked_processing(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write("a,b\n")
            for i in range(100):
                f.write(f"{i},{i*2}\n")
            tmp_path = f.name

        try:
            out_path, report = preprocess_csv(tmp_path, chunksize=30, generate_report=True)
            df = pd.read_csv(out_path)
            self.assertEqual(len(df), 100)
        finally:
            os.unlink(tmp_path)
            for ext in [".bak", ".report.json"]:
                if os.path.exists(tmp_path + ext):
                    os.unlink(tmp_path + ext)
