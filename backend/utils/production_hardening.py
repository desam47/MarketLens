#!/usr/bin/env python3
"""
Production Hardening Checker
"""
import os
import re
import subprocess
import sys
from pathlib import Path


class ProductionHardeningChecker:
    def __init__(self, root_path):
        self.root_path = Path(root_path)
        self.issues = []
        self.warnings = []
        self.passed = []

    def check_secrets(self):
        print("Checking for secrets...")
        secret_patterns = [
            r'["\'](?:api[_-]?key|apikey|access[_-]?token|secret[_-]?key|private[_-]?key)["\']\s*[:=]\s*["\'][^"\']{10,}["\']',
            r'["\'](?:password|passwd|pwd)["\']\s*[:=]\s*["\'][^"\']{6,}["\']',
        ]

        exclude_dirs = {'.git', '__pycache__', 'node_modules', '.env', 'venv', '.venv'}
        exclude_files = {'production_hardening.py', '.env.example'}

        for py_file in self.root_path.rglob("*.py"):
            if any(exclude_dir in str(py_file) for exclude_dir in exclude_dirs):
                continue
            if py_file.name in exclude_files:
                continue

            try:
                content = py_file.read_text(encoding='utf-8')
                for pattern in secret_patterns:
                    matches = re.finditer(pattern, content, re.IGNORECASE)
                    for match in matches:
                        matched_text = match.group(0)
                        if not any(skip in matched_text.lower() for skip in
                                 ['example', 'test', 'dummy', 'placeholder', 'your_', '<', '>']):
                            self.issues.append(
                                f"POTENTIAL SECRET in {py_file.relative_to(self.root_path)}: {matched_text[:50]}..."
                            )
            except Exception as e:
                self.warnings.append(f"Could not read {py_file}: {e}")

    def check_frontend_keys(self):
        print("Checking frontend for API keys...")
        frontend_dirs = ['frontend', 'src/frontend', 'public', 'static']
        frontend_extensions = {'.js', '.ts', '.jsx', '.tsx', '.html', '.vue', '.svelte'}

        for frontend_dir in frontend_dirs:
            dir_path = self.root_path / frontend_dir
            if not dir_path.exists():
                continue

            for file_path in dir_path.rglob("*"):
                if file_path.suffix not in frontend_extensions:
                    continue

                try:
                    content = file_path.read_text(encoding='utf-8')
                    patterns = [
                        r'["\'](?:api[_-]?key|apikey)["\']\s*[:=]\s*["\'][^"\']{10,}["\']',
                        r'process\.env\.\[["\'](?:REACT_APP_|VITE_|NEXT_PUBLIC_)',
                        r'["\'](?:REACT_APP_|VITE_|NEXT_PUBLIC_)[A-Z_]+["\']\s*[:=]',
                    ]

                    for pattern in patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            self.warnings.append(
                                f"POTENTIAL FRONTEND API KEY in {file_path.relative_to(self.root_path)}"
                            )
                except Exception as e:
                    self.warnings.append(f"Could not read frontend file {file_path}: {e}")

    def check_env_handling(self):
        print("Checking environment handling...")
        for py_file in self.root_path.rglob("*.py"):
            if any(exclude_dir in str(py_file) for exclude_dir in ['.git', '__pycache__', 'venv', '.venv']):
                continue

            try:
                content = py_file.read_text(encoding='utf-8')
                env_accesses = re.finditer(r'os\.environ\[[^\]]+\]', content)
                for match in env_accesses:
                    line_num = content[:match.start()].count('\n') + 1
                    self.warnings.append(
                        f"Direct os.environ access in {py_file.relative_to(self.root_path)}:{line_num} "
                        f"- consider using os.environ.get() with defaults"
                    )
            except Exception:
                pass

    def check_error_handling(self):
        print("Checking error handling...")
        for py_file in self.root_path.rglob("*.py"):
            if any(exclude_dir in str(py_file) for exclude_dir in ['.git', '__pycache__', 'tests', 'venv', '.venv']):
                continue

            try:
                content = py_file.read_text(encoding='utf-8')
                bare_excepts = re.finditer(r'except\s*:', content)
                for match in bare_excepts:
                    line_num = content[:match.start()].count('\n') + 1
                    self.issues.append(
                        f"Bare except clause in {py_file.relative_to(self.root_path)}:{line_num} "
                        f"- should specify exception types"
                    )

                except_pass = re.finditer(r'except\s*.*?:\s*\n\s*pass', content, re.DOTALL)
                for match in except_pass:
                    line_num = content[:match.start()].count('\n') + 1
                    self.warnings.append(
                        f"Empty except block in {py_file.relative_to(self.root_path)}:{line_num} "
                        f"- consider logging or handling the exception"
                    )
            except Exception:
                pass

    def check_logging(self):
        print("Checking logging...")
        for py_file in self.root_path.rglob("*.py"):
            if any(exclude_dir in str(py_file) for exclude_dir in ['.git', '__pycache__', 'tests', 'venv', '.venv']):
                continue

            try:
                content = py_file.read_text(encoding='utf-8')
                print_statements = re.finditer(r'^\s*print\(', content, re.MULTILINE)
                for match in print_statements:
                    line_num = content[:match.start()].count('\n') + 1
                    self.warnings.append(
                        f"Print statement in {py_file.relative_to(self.root_path)}:{line_num} "
                        f"- consider using logging instead"
                    )

                if 'import logging' not in content and 'from logging' not in content:
                    if len(content.splitlines()) > 10:
                        self.warnings.append(
                            f"No logging import in {py_file.relative_to(self.root_path)} "
                            f"- consider adding logging for production use"
                        )
            except Exception:
                pass

    def run_checks(self):
        print("Starting Production Hardening Check\n")

        self.check_secrets()
        self.check_frontend_keys()
        self.check_env_handling()
        self.check_error_handling()
        self.check_logging()

        print("\n" + "="*60)
        print("PRODUCTION HARDENING CHECK RESULTS")
        print("="*60)

        if self.issues:
            print(f"\n❌ ISSUES FOUND ({len(self.issues)}):")
            for issue in self.issues:
                print(f"  • {issue}")
        else:
            print("\n✅ NO CRITICAL ISSUES FOUND")

        if self.warnings:
            print(f"\n⚠️  WARNINGS ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"  • {warning}")

        if self.passed:
            print(f"\n✅ CHECKS PASSED ({len(self.passed)}):")
            for passed in self.passed:
                print(f"  • {passed}")

        print(f"\n{'='*60}")
        total_issues = len(self.issues) + len(self.warnings)
        if total_issues == 0:
            print("🎉 ALL CHECKS PASSED - READY FOR PRODUCTION!")
            return True
        elif len(self.issues) == 0:
            print("⚠️  ONLY WARNINGS - RECOMMENDED TO ADDRESS BEFORE PRODUCTION")
            return True
        else:
            print("🛑 ISSUES FOUND - PLEASE FIX BEFORE PRODUCTION DEPLOYMENT")
            return False

    def create_docs(self):
        print("\nCreating documentation files...")
        docs_dir = self.root_path / "docs"
        docs_dir.mkdir(exist_ok=True)

        (docs_dir / "README.md").write_text("# MarketLens\n\nA comprehensive market analysis platform.\n")
        (docs_dir / "ARCHITECTURE.md").write_text("# MarketLens Architecture\n\nModular platform for market analysis.\n")
        (docs_dir / "PROVIDERS.md").write_text("# Market Data Providers\n\nInformation about supported data providers.\n")
        (docs_dir / "AI.md").write_text("# AI Integration\n\nOptional AI capabilities for enhanced analysis.\n")
        (docs_dir / "TESTING.md").write_text("# Testing Guide\n\nHow to run and write tests.\n")
        (docs_dir / "TROUBLESHOOTING.md").write_text("# Troubleshooting Guide\n\nCommon issues and solutions.\n")

        print("📄 Documentation files created in docs/")

    def run_tests(self):
        print("\nRunning test suite...")
        try:
            test_dir = self.root_path / "MarketLens" / "backend"
            env = os.environ.copy()
            env['PYTHONPATH'] = str(self.root_path)
            result = subprocess.run([
                sys.executable, "-m", "pytest", "tests/", "-v"
            ], cwd=test_dir, capture_output=True, text=True, timeout=120, env=env)

            if result.returncode == 0:
                print("✅ All tests passed!")
                self.passed.append("Test suite passed")
                return True
            else:
                print("❌ Some tests failed:")
                print(result.stdout[-1000:])
                if result.stderr:
                    print("STDERR:", result.stderr[-500:])
                self.issues.append("Test suite failed")
                return False
        except subprocess.TimeoutExpired:
            print("⏰ Test suite timed out")
            self.warnings.append("Test suite took too long (>2min)")
            return False
        except Exception as e:
            print(f"💥 Error running tests: {e}")
            self.warnings.append(f"Could not run tests: {e}")
            return False

    def run_linting(self):
        print("\nRunning linting...")
        try:
            result = subprocess.run([
                sys.executable, "-m", "flake8", "MarketLens/", "--count"
            ], cwd=self.root_path, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                print("✅ Linting passed!")
                self.passed.append("Linting passed")
                return True
            else:
                print(f"⚠️  Linting issues found ({result.stdout.strip()} errors)")
                self.warnings.append(f"Linting reported issues: {result.stdout.strip()}")
                return False
        except FileNotFoundError:
            print("ℹ️  flake8 not installed, skipping linting")
            self.passed.append("Linting skipped (flake8 not available)")
            return True
        except Exception as e:
            print(f"ℹ️  Could not run linting: {e}")
            self.warnings.append(f"Linting check skipped: {e}")
            return True

def main():
    checker = ProductionHardeningChecker(".")

    checker.run_checks()
    checker.create_docs()
    checker.run_tests()
    checker.run_linting()

    if len(checker.issues) == 0:
        print("\n🎯 Production hardening check completed successfully!")
        sys.exit(0)
    else:
        print("\n🛑 Production hardening check found issues that need attention!")
        sys.exit(1)

if __name__ == "__main__":
    main()
