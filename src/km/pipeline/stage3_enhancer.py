"""Stage 3 AI-enhanced JSON generation pipeline implementation."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..utils.openai_client import get_openai_client, get_model_name
from .config import Stage3Config

logger = logging.getLogger(__name__)


class Stage3Enhancer:
    """Stage 3 AI JSON enhancer."""
    
    def __init__(self, config: Stage3Config):
        """Initialize enhancer with configuration.
        
        Args:
            config: Stage3 configuration
        """
        self.config = config
        self.logger = self._setup_logger()
        
        # Initialize OpenAI or Azure OpenAI client
        self.client, self._is_azure = get_openai_client(timeout=1500.0, purpose="chat")
        self._model_name = get_model_name(purpose="chat", is_azure=self._is_azure)
        self.logger.info(f"Using model: {self._model_name}, azure={self._is_azure}")
        
        self.progress = self.load_progress()
        self.results = {
            "start_time": datetime.now().isoformat(),
            "statistics": {
                "total": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
            },
            "failed_files": [],
        }
    
    def _setup_logger(self) -> logging.Logger:
        """Set up file-specific logger."""
        logger = logging.getLogger(f"{__name__}.{id(self)}")
        logger.setLevel(logging.INFO)
        
        # Remove any existing handlers
        logger.handlers.clear()
        
        # Add file handler
        if self.config.log_path:
            handler = logging.FileHandler(self.config.log_path)
            handler.setFormatter(
                logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            )
            logger.addHandler(handler)
        
        logger.propagate = False
        return logger
    
    def load_progress(self) -> Dict[str, Any]:
        """Load progress from previous runs."""
        if self.config.progress_path.exists():
            try:
                with open(self.config.progress_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                self.logger.warning(f"Could not load progress: {e}")
        
        return {
            "processed_files": [],
            "last_file": None,
            "timestamp": None,
        }
    
    def save_progress(self) -> None:
        """Save current progress."""
        self.progress["timestamp"] = datetime.now().isoformat()
        
        try:
            with open(self.config.progress_path, 'w', encoding='utf-8') as f:
                json.dump(self.progress, f, indent=2, ensure_ascii=False)
        except OSError as e:
            self.logger.error(f"Could not save progress: {e}")
    
    def collect_target_files(self) -> List[Path]:
        """Collect JSON files to process."""
        if self.config.paths_file and self.config.paths_file.exists():
            # Read specific paths from file
            with open(self.config.paths_file, 'r', encoding='utf-8') as f:
                paths = [line.strip() for line in f if line.strip()]
            
            files = []
            for path in paths:
                file_path = self.config.input_dir / path
                if file_path.exists() and file_path.suffix == '.json':
                    files.append(file_path)
            
            self.logger.info(f"Found {len(files)} files from paths file")
            return sorted(files)
        
        # Collect all .json files
        if not self.config.input_dir.exists():
            self.logger.warning(f"Input directory does not exist: {self.config.input_dir}")
            return []
        
        files = list(self.config.input_dir.rglob("*.json"))
        
        # Filter out already processed files if not retrying
        if not self.config.retry_failed:
            processed = set(self.progress.get("processed_files", []))
            files = [f for f in files if str(f) not in processed]
        
        self.logger.info(f"Found {len(files)} JSON files to process")
        return sorted(files)
    
    def create_enhancement_prompt(self, content: str) -> str:
        """Create prompt for AI enhancement.
        
        Args:
            content: Document content to analyze
            
        Returns:
            Formatted prompt
        """
        prompt = f"""以下のドキュメントを詳細に分析し、JSON形式で情報を抽出してください。

ドキュメント内容:
{content[:100000]}

以下の情報を網羅的に抽出してください:

1. summary_short: ドキュメントの概要を100文字程度で説明（製品名・型番・主要仕様を含む）
2. summary_long: ドキュメントの詳細な概要を400-500文字で説明。以下を含めること：
   - 製品名・型番・シリーズ名
   - 技術仕様（寸法・重量・性能値・耐荷重・積雪量等の具体的数値）
   - 適用条件・基準値・閾値（例：10kW、積雪量60/99/150cm等）
   - 法規制・標準規格（建築基準法・電気事業法等）
   - 価格情報・見積金額・単価
   - 業務フロー・販促プロセス
   - オプション構成・バリエーション
   - 製品間の強度差・仕様差の比較情報
3. companies: ドキュメントに登場する企業名のリスト（配列形式、最大10社）
4. products: ドキュメントに登場する製品名・型番のリスト（配列形式、最大15製品）
5. file_type: ドキュメントの種別（例：契約書、提案書、仕様書、見積書、報告書、議事録、マニュアル、プレゼンテーション、価格表、勉強会資料、その他）

必ずJSON形式で回答してください:
{{
    "summary_short": "100文字程度の概要（製品名・型番・主要仕様を含む）",
    "summary_long": "400-500文字の詳細な概要（技術詳細・適用条件・法規制・フロー・数値データ等を含む）",
    "companies": ["企業名1", "企業名2", ...],
    "products": ["製品名1/型番1", "製品名2/型番2", ...],
    "file_type": "ドキュメント種別"
}}
"""
        return prompt
    
    def call_gpt_enhancement(
        self, 
        content: str,
        retries: int = 3
    ) -> Optional[Dict[str, Any]]:
        """Call GPT for content enhancement.
        
        Args:
            content: Content to enhance
            retries: Number of retry attempts
            
        Returns:
            Enhancement data or None if failed
        """
        prompt = self.create_enhancement_prompt(content)
        
        # Use model from environment if config has default, otherwise use config
        model = self._model_name if self.config.model == "gpt-5" else self.config.model
        
        for attempt in range(retries):
            try:
                self.logger.info(f"Calling OpenAI API (attempt {attempt + 1}/{retries})")
                
                # gpt-5系/Azure OpenAI対応
                if "gpt-5" in model or "o1" in model or "o3" in model or getattr(self, '_is_azure', False):
                    params = {
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "あなたは日本語のビジネスドキュメントを分析し、構造化された情報を抽出する専門家です。"
                            },
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        "max_completion_tokens": self.config.max_tokens,
                        "response_format": {"type": "json_object"}
                    }
                    # gpt-5.1の場合のみreasoning=noneとtemperature=0を追加
                    if "5.1" in model or "5-1" in model:
                        params["temperature"] = 0
                    response = self.client.chat.completions.create(**params)
                else:
                    response = self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": "あなたは日本語のビジネスドキュメントを分析し、構造化された情報を抽出する専門家です。"
                            },
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
                        response_format={"type": "json_object"}
                    )
                
                # Parse response
                result = json.loads(response.choices[0].message.content)
                
                # Validate required fields
                required_fields = ["summary_short", "summary_long", "companies", "file_type"]
                if all(field in result for field in required_fields):
                    # Ensure character limits
                    if len(result["summary_short"]) > 40:
                        result["summary_short"] = result["summary_short"][:40]
                    if len(result["summary_long"]) > 150:
                        result["summary_long"] = result["summary_long"][:150]
                    
                    return result
                else:
                    self.logger.warning(f"Missing required fields in response")
                    
            except json.JSONDecodeError as e:
                self.logger.error(f"Failed to parse JSON response: {e}")
            except Exception as e:
                self.logger.error(f"API call failed (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
        
        return None
    
    def enhance_json(self, json_path: Path) -> Dict[str, Any]:
        """Enhance a JSON file with AI analysis.
        
        Args:
            json_path: Path to JSON file
            
        Returns:
            Enhanced JSON data
        """
        try:
            # Read existing JSON
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Extract content for analysis
            content = ""
            # Check various possible content fields
            if "text" in data:
                content = data["text"]
            elif "content" in data:
                if "full_text" in data["content"]:
                    content = data["content"]["full_text"]
                elif "sections" in data["content"]:
                    # Combine all sections
                    sections = data["content"]["sections"]
                    content = "\n".join([s.get("content", "") for s in sections])
            
            if not content:
                self.logger.warning(f"No content found in {json_path}")
                return data

            # Debug: Log content extraction
            self.logger.info(f"Content extracted, length: {len(content)}")

            # Get AI enhancement
            enhancement = self.call_gpt_enhancement(content)

            # Debug: Log enhancement result
            self.logger.info(f"Enhancement result: {enhancement is not None}")

            if enhancement:
                # Add enhancement to data
                data["ai_enhancement"] = {
                    "summary_short": enhancement["summary_short"],
                    "summary_long": enhancement["summary_long"],
                    "companies": enhancement["companies"],
                    "file_type": enhancement["file_type"],
                    "enhancement_timestamp": datetime.now().isoformat(),
                    "model": self.config.model,
                }
                
                # Update processing info (create if doesn't exist)
                if "processing" not in data:
                    data["processing"] = {}
                data["processing"]["stage"] = "ai_enhanced"
                data["processing"]["enhancement_version"] = "1.0"
            
            return data
            
        except Exception as e:
            self.logger.error(f"Error enhancing {json_path}: {e}")
            raise
    
    def process_file(self, json_path: Path) -> Dict[str, Any]:
        """Process a single JSON file.
        
        Args:
            json_path: Path to JSON file
            
        Returns:
            Processing result
        """
        # Ensure json_path is absolute
        json_path = json_path.resolve()
        
        # Calculate relative path
        try:
            relative_path = json_path.relative_to(self.config.input_dir)
        except ValueError:
            # If paths don't match, use just the filename
            relative_path = Path(json_path.name)
        
        # Check if already processed
        if str(json_path) in self.progress.get("processed_files", []):
            return {
                "file": str(relative_path),
                "status": "skipped",
                "reason": "already_processed",
            }
        
        try:
            # Enhance the JSON
            enhanced = self.enhance_json(json_path)
            
            # Save enhanced JSON
            output_path = self.config.output_dir / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(enhanced, f, indent=2, ensure_ascii=False)
            
            # Update progress
            self.progress.setdefault("processed_files", []).append(str(json_path))
            self.progress["last_file"] = str(json_path)
            
            self.logger.info(f"Enhanced: {relative_path}")
            
            return {
                "file": str(relative_path),
                "status": "success",
                "output": str(output_path),
                "enhancement": enhanced.get("ai_enhancement", {}),
            }
            
        except Exception as e:
            self.logger.error(f"Failed to process {relative_path}: {e}")
            
            return {
                "file": str(relative_path),
                "status": "failed",
                "error": str(e),
            }
    
    def run(
        self,
        batch_size: int = 10,
        max_files: Optional[int] = None
    ) -> int:
        """Run the enhancement process.
        
        Args:
            batch_size: Number of files to process before saving progress
            max_files: Maximum number of files to process
            
        Returns:
            Exit code (0 for success)
        """
        self.logger.info("Starting Stage 3 AI enhancement")
        
        # Collect target files
        files = self.collect_target_files()
        
        if max_files:
            files = files[:max_files]
        
        self.results["statistics"]["total"] = len(files)
        
        if not files:
            self.logger.info("No files to process")
            self.save_results()
            return 0
        
        # Process files
        for i, json_path in enumerate(files, 1):
            self.logger.info(f"Processing {i}/{len(files)}: {json_path.name}")
            
            result = self.process_file(json_path)
            
            # Update statistics
            if result["status"] == "success":
                self.results["statistics"]["success"] += 1
            elif result["status"] == "skipped":
                self.results["statistics"]["skipped"] += 1
            else:
                self.results["statistics"]["failed"] += 1
                self.results["failed_files"].append(result)
            
            # Save progress periodically
            if i % batch_size == 0:
                self.save_progress()
                self.logger.info(f"Progress saved at {i}/{len(files)}")
        
        # Final save
        self.save_progress()
        self.save_results()
        
        self.logger.info(
            f"Completed: {self.results['statistics']['success']} success, "
            f"{self.results['statistics']['failed']} failed, "
            f"{self.results['statistics']['skipped']} skipped"
        )
        
        return 0 if self.results["statistics"]["failed"] == 0 else 1
    
    def save_results(self) -> None:
        """Save final results."""
        self.results["end_time"] = datetime.now().isoformat()
        
        try:
            with open(self.config.results_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"Results saved to {self.config.results_path}")
        except OSError as e:
            self.logger.error(f"Could not save results: {e}")