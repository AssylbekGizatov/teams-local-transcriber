import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "build" / "smoke.py"
spec = importlib.util.spec_from_file_location("build_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class EnvironmentTests(unittest.TestCase):
    def test_systemroot_case_and_path_isolation(self):
        for key in ("SystemRoot", "SYSTEMROOT", "systemroot"):
            source = {key: "C:/Windows", "Path": "C:/Python;C:/tools", "PythonPath": "private", "PythonHome": "private", "Virtual_Env": "private", "Tcl_Library": "private", "Tk_Library": "private", "sd_enable_asio": "1", "TEMP": "C:/Temp"}
            env = smoke.isolated_environment(source, Path("cache"))
            self.assertEqual(env["PATH"], str(Path("C:/Windows") / "System32"))
            self.assertEqual(env["SYSTEMROOT"], "C:/Windows")
            self.assertEqual(env["TEMP"], "C:/Temp")
            for forbidden in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "TCL_LIBRARY", "TK_LIBRARY", "SD_ENABLE_ASIO", "Path"):
                self.assertNotIn(forbidden, env)
            self.assertEqual(env["PYTHONNOUSERSITE"], "1")
            self.assertIn("Path", source)  # Input remains unchanged.

    def test_windir_fallback(self):
        env = smoke.isolated_environment({"windir": "C:/Windows"}, Path("cache"))
        self.assertEqual(env["PATH"], str(Path("C:/Windows") / "System32"))

    def test_missing_windows_root_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "SystemRoot/WINDIR"):
            smoke.isolated_environment({"PATH": "C:/Python"}, Path("cache"))


if __name__ == "__main__":
    unittest.main()
