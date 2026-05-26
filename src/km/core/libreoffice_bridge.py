"""LibreOffice conversion bridge for unified document processing."""

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class LibreOfficeConversionError(Exception):
    """Raised when LibreOffice conversion fails."""
    pass


def _find_soffice() -> str:
    """Find soffice executable path.

    Returns:
        Path to soffice executable, or "soffice" if not found (rely on PATH)
    """
    # Windows default paths
    windows_paths = [
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ]

    # macOS default path
    mac_paths = [
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ]

    # Linux default paths
    linux_paths = [
        Path("/usr/bin/soffice"),
        Path("/usr/local/bin/soffice"),
    ]

    import platform
    system = platform.system()

    if system == "Windows":
        candidates = windows_paths
    elif system == "Darwin":
        candidates = mac_paths
    else:
        candidates = linux_paths

    for path in candidates:
        if path.exists():
            logger.debug(f"Found LibreOffice at: {path}")
            return str(path)

    # Fallback to PATH
    return "soffice"


class LibreOfficeBridge:
    """Unified LibreOffice interface for document conversion.

    This class centralizes all LibreOffice interactions, providing:
    - Consistent error handling
    - Unified logging to lo_runs directory
    - Profile management for concurrent operations
    - Support for multiple output formats
    """

    def __init__(self, soffice_path: Optional[str] = None, lo_runs_dir: Optional[Path] = None):
        """Initialize LibreOffice bridge.

        Args:
            soffice_path: Path to soffice executable (auto-detected if None)
            lo_runs_dir: Directory for LibreOffice run logs (default: logs/lo_runs)
        """
        self.soffice_path = soffice_path or _find_soffice()
        self.lo_runs_dir = lo_runs_dir or Path("logs/lo_runs")
        self.lo_runs_dir.mkdir(parents=True, exist_ok=True)
        
    def convert_to_pdf(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        timeout: int = 600  # 10分に延長（大きいPPTX対応）
    ) -> Tuple[Path, bool, Path]:
        """Convert document to PDF using LibreOffice.
        
        Args:
            input_path: Path to input document
            output_path: Optional output PDF path (auto-generated if None)
            timeout: Conversion timeout in seconds
            
        Returns:
            Tuple of (pdf_path, success, log_path)
        """
        input_path = Path(input_path).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")
            
        # Create temporary directory for conversion
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            profile_dir = tmpdir_path / "lo_profile"
            profile_dir.mkdir()
            
            # Prepare output path
            if output_path:
                output_path = Path(output_path).resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                cleanup_temp = False
            else:
                # Create a stable temporary file outside the conversion directory
                import tempfile as tf
                fd, temp_path = tf.mkstemp(suffix='.pdf', prefix=f"{input_path.stem}_")
                os.close(fd)  # Close the file descriptor
                output_path = Path(temp_path)
                cleanup_temp = False  # Caller is responsible for cleanup
                
            # Log conversion attempt
            log_path = self._prepare_log_path(input_path)
            
            try:
                # Run LibreOffice conversion
                result = self._run_conversion(
                    input_path,
                    tmpdir_path,
                    profile_dir,
                    "pdf",
                    timeout,
                    log_path
                )
                self._log_command_result(log_path, result)
                
                # Find generated PDF
                pdf_files = list(tmpdir_path.glob("*.pdf"))
                if not pdf_files:
                    self._log_error(log_path, f"No PDF generated for {input_path.name}")
                    return output_path, False, log_path
                    
                # Move to final location
                generated_pdf = pdf_files[0]
                if output_path != generated_pdf:
                    shutil.move(str(generated_pdf), str(output_path))
                    
                self._log_success(log_path, f"Successfully converted {input_path.name}")
                return output_path, True, log_path
                
            except subprocess.TimeoutExpired:
                self._log_error(log_path, f"Conversion timeout for {input_path.name}")
                return output_path, False, log_path
            except Exception as e:
                self._log_error(log_path, f"Conversion failed: {e}")
                return output_path, False, log_path
                
    def convert_to_xlsx(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        timeout: int = 600  # 10分に延長
    ) -> Tuple[Path, bool]:
        """Convert legacy Excel to XLSX format.
        
        Args:
            input_path: Path to .xls file
            output_path: Optional output XLSX path
            timeout: Conversion timeout in seconds
            
        Returns:
            Tuple of (xlsx_path, success)
        """
        input_path = Path(input_path).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")
            
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            profile_dir = tmpdir_path / "lo_profile"
            profile_dir.mkdir()
            
            if output_path:
                output_path = Path(output_path).resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                # Create a stable temporary file outside the conversion directory
                import tempfile as tf
                fd, temp_path = tf.mkstemp(suffix='.xlsx', prefix=f"{input_path.stem}_")
                os.close(fd)  # Close the file descriptor
                output_path = Path(temp_path)
                
            log_path = self._prepare_log_path(input_path)
            
            try:
                # Run conversion to xlsx:xlsx (Excel 2007-365 format)
                result = self._run_conversion(
                    input_path,
                    tmpdir_path,
                    profile_dir,
                    "xlsx:Calc MS Excel 2007 XML",
                    timeout,
                    log_path
                )
                
                # Find generated XLSX
                xlsx_files = list(tmpdir_path.glob("*.xlsx"))
                if not xlsx_files:
                    self._log_error(log_path, f"No XLSX generated for {input_path.name}")
                    return output_path, False
                    
                generated_xlsx = xlsx_files[0]
                if output_path != generated_xlsx:
                    shutil.move(str(generated_xlsx), str(output_path))
                    
                self._log_success(log_path, f"Successfully converted {input_path.name}")
                return output_path, True
                
            except Exception as e:
                self._log_error(log_path, f"XLSX conversion failed: {e}")
                return output_path, False
                
    def convert_to_docx(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        timeout: int = 600  # 10分に延長
    ) -> Tuple[Path, bool]:
        """Convert legacy DOC to DOCX format.
        
        Args:
            input_path: Path to .doc file
            output_path: Optional output DOCX path
            timeout: Conversion timeout in seconds
            
        Returns:
            Tuple of (docx_path, success)
        """
        input_path = Path(input_path).resolve()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            profile_dir = tmpdir_path / "lo_profile"
            profile_dir.mkdir()
            
            if output_path:
                output_path = Path(output_path).resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                # Create a stable temporary file outside the conversion directory
                import tempfile as tf
                fd, temp_path = tf.mkstemp(suffix='.docx', prefix=f"{input_path.stem}_")
                os.close(fd)  # Close the file descriptor
                output_path = Path(temp_path)
                
            log_path = self._prepare_log_path(input_path)
            
            try:
                # Run conversion to docx
                result = self._run_conversion(
                    input_path,
                    tmpdir_path,
                    profile_dir,
                    "docx:MS Word 2007 XML",
                    timeout,
                    log_path
                )
                
                # Find generated DOCX
                docx_files = list(tmpdir_path.glob("*.docx"))
                if not docx_files:
                    self._log_error(log_path, f"No DOCX generated for {input_path.name}")
                    return output_path, False
                    
                generated_docx = docx_files[0]
                if output_path != generated_docx:
                    shutil.move(str(generated_docx), str(output_path))
                    
                self._log_success(log_path, f"Successfully converted {input_path.name}")
                return output_path, True
                
            except Exception as e:
                self._log_error(log_path, f"DOCX conversion failed: {e}")
                return output_path, False
    
    def _run_conversion(
        self,
        input_path: Path,
        output_dir: Path,
        profile_dir: Path,
        format_spec: str,
        timeout: int,
        log_path: Path
    ) -> subprocess.CompletedProcess:
        """Execute LibreOffice conversion command.

        Args:
            input_path: Input file path
            output_dir: Output directory
            profile_dir: LibreOffice profile directory
            format_spec: Output format specification (e.g., "pdf", "xlsx:Calc MS Excel 2007 XML")
            timeout: Command timeout in seconds
            log_path: Path for logging

        Returns:
            Subprocess result
        """
        # Copy input file to temp dir to avoid path encoding issues (Japanese chars, spaces, etc.)
        temp_input = output_dir / f"input{input_path.suffix}"
        shutil.copy2(input_path, temp_input)

        # Build file URI for UserInstallation (Windows needs file:/// prefix)
        import platform
        if platform.system() == "Windows":
            profile_uri = f"file:///{profile_dir.absolute().as_posix()}"
        else:
            profile_uri = f"file://{profile_dir.absolute()}"

        cmd = [
            self.soffice_path,
            "--headless",
            "--invisible",
            "--nodefault",
            "--nolockcheck",
            "--nologo",
            "--norestore",
            "--convert-to",
            format_spec,
            "--outdir",
            str(output_dir),
            f"-env:UserInstallation={profile_uri}",
            str(temp_input)
        ]

        # Log command
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] Original file: {input_path}\n")
            f.write(f"[{datetime.now().isoformat()}] Command: {' '.join(cmd)}\n")

        # Run conversion
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(output_dir)
        )

        # Clean up temp input
        try:
            temp_input.unlink()
        except Exception:
            pass

        # Log output
        with log_path.open("a", encoding="utf-8") as f:
            if result.stdout:
                f.write(f"STDOUT: {result.stdout}\n")
            if result.stderr:
                f.write(f"STDERR: {result.stderr}\n")
            f.write(f"Return code: {result.returncode}\n")

        return result

    def _log_command_result(self, log_path: Path, result: subprocess.CompletedProcess) -> None:
        """Log LibreOffice stdout/stderr to application logger for quick triage."""
        logger.debug(f"LibreOffice return code: {result.returncode} (log: {log_path})")
        if result.stdout:
            logger.debug(f"LibreOffice STDOUT for {log_path.name}:\n{result.stdout}")
        if result.stderr:
            logger.debug(f"LibreOffice STDERR for {log_path.name}:\n{result.stderr}")
    
    def _prepare_log_path(self, input_path: Path) -> Path:
        """Prepare log file path for conversion.
        
        Args:
            input_path: Input file being converted
            
        Returns:
            Path to log file
        """
        today = datetime.now().strftime("%Y%m%d")
        log_dir = self.lo_runs_dir / today
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Sanitize filename for log
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in input_path.name)
        log_path = log_dir / f"{safe_name}.log"
        
        return log_path
    
    def _log_success(self, log_path: Path, message: str):
        """Log successful conversion."""
        with log_path.open("a") as f:
            f.write(f"[{datetime.now().isoformat()}] SUCCESS: {message}\n")
        logger.info(message)
        
    def _log_error(self, log_path: Path, message: str):
        """Log conversion error."""
        with log_path.open("a") as f:
            f.write(f"[{datetime.now().isoformat()}] ERROR: {message}\n")
        logger.error(message)
        
    def extract_text_with_pdftotext(self, pdf_path: Path) -> str:
        """Extract text from PDF using pdftotext utility.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Extracted text or empty string on failure
        """
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(pdf_path), "-"],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.error(f"pdftotext failed: {result.stderr}")
                return ""
        except subprocess.TimeoutExpired:
            logger.error(f"pdftotext timeout for {pdf_path}")
            return ""
        except Exception as e:
            logger.error(f"pdftotext error: {e}")
            return ""
