"""Proposal Generator - ナレッジ更新提案を生成.

既存ナレッジと新しいドキュメントを照合し、
最適な更新アクション（新規追加/更新/スキップ）を判定・提案する。
AI（gpt-5.1）を使用して類似度計算・配置先提案を行う。
Embedding事前フィルタにより、類似度計算の効率化を実現。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .knowledge_config import (
    FOLDER_FORMATTED_MARKDOWN,
    FILE_ENHANCED_MD,
)

# Embedding設定
EMBEDDING_CACHE_FILE = ".embeddings.json"
EMBEDDING_MODEL = "text-embedding-3-large"  # デフォルトモデル
EMBEDDING_TOP_K = 10  # 類似度上位N件をAIで詳細比較


logger = logging.getLogger(__name__)


class ActionType(Enum):
    """更新アクションの種別"""
    CREATE = "create"           # 新規ファイル作成
    UPDATE = "update"           # 既存ファイル更新（追記・編集含む）
    PARTIAL_UPDATE = "partial"  # 部分更新（一部を既存に更新、残りを新規作成）
    SKIP = "skip"               # スキップ（変更なし / 除外）


class UpdateMode(Enum):
    """更新モード"""
    NEW = "new"                    # 新規作成（既存ナレッジなし）
    INCREMENTAL = "incremental"   # 既存ナレッジ利用（増分更新）
    RESTRUCTURE = "restructure"   # 既存ナレッジ抜本的変更


@dataclass
class ChangeDetail:
    """変更詳細"""
    item: str                       # 更新項目（フロー名、セクション名など）
    target_file: str               # 更新先ファイル
    target_line: str               # 更新行（例: "407-432行（9.4節）"）
    before_content: str            # 更新前内容（抜粋）
    after_content: str             # 更新後内容
    is_primary: bool = True        # True=主要変更, False=派生変更


@dataclass
class PartialUpdateInfo:
    """部分更新の情報"""
    update_sections: List[str]      # 更新対象セクション名
    update_target: str              # 更新先ファイルパス
    update_char_count: int          # 更新する文字数
    create_sections: List[str]      # 新規作成対象セクション名
    create_target: str              # 新規作成先ファイルパス
    create_char_count: int          # 新規作成する文字数


@dataclass
class TargetFileInfo:
    """更新先ファイルの情報（複数ファイル対応）"""
    file_path: str                          # ファイルパス
    action: str                             # "update" or "create"
    update_sections: List[str] = field(default_factory=list)  # 更新するセクション名（内容変更あり）
    update_lines: Dict[str, str] = field(default_factory=dict)  # セクション名 → 行番号
    new_sections: List[str] = field(default_factory=list)      # 新規追加するセクション名
    unchanged_sections: List[str] = field(default_factory=list)  # 変更なしセクション名（内容同一）
    similarity: float = 0.0                 # このファイルとの類似度


@dataclass
class MultiTargetUpdate:
    """複数ファイルへの更新情報"""
    targets: List[TargetFileInfo] = field(default_factory=list)  # 更新先ファイル一覧
    total_sections: int = 0                 # 入力資料の総セクション数
    unchanged_sections: List[str] = field(default_factory=list)  # 変更なしセクション名（全体）
    

@dataclass
class ProposedAction:
    """1つの更新アクション"""
    action_type: ActionType
    source_document: str            # 入力資料名
    target_path: Optional[str]      # 更新対象のパス（スキップの場合はNone）
    reason: str                     # 判断理由
    similarity: float = 0.0         # 類似度（0.0-1.0）
    changes: List[ChangeDetail] = field(default_factory=list)  # 変更詳細
    # 新規追加時の分割情報
    split_into: List[str] = field(default_factory=list)  # 分割先パス一覧
    # 部分更新時の情報
    partial_info: Optional[PartialUpdateInfo] = None
    # 複数ファイルへの更新情報（Stage 2で使用）
    multi_target: Optional[MultiTargetUpdate] = None


@dataclass
class Proposal:
    """更新提案全体"""
    mode: UpdateMode                        # 更新モード
    actions: List[ProposedAction]           # アクション一覧
    excluded_documents: List[str] = field(default_factory=list)  # 除外されたドキュメント
    summary: Dict[str, int] = field(default_factory=dict)  # サマリー統計
    needs_restructure: bool = False         # 抜本的変更が必要か
    restructure_reason: str = ""            # 抜本的変更の理由


class ProposalGenerator:
    """更新提案を生成するクラス（AI使用）"""
    
    # 類似度の閾値（80%で統一）
    SIMILARITY_THRESHOLD = 0.80          # 80%以上で更新、80%未満は新規
    
    # 抜本的変更を提案する条件
    MAX_FILES_PER_CATEGORY = 12          # カテゴリ内ファイル数の上限
    MAX_TOTAL_CHARS_PER_CATEGORY = 500000  # カテゴリ内文字数の上限
    
    # ファイルサイズの閾値
    LARGE_FILE_THRESHOLD = 50000         # 5万文字以上で分割を検討（新規作成時）
    UPDATE_SPLIT_THRESHOLD = 10000       # 1万文字以上でマージ後分割を検討（更新時）
    
    def __init__(
        self,
        pre_knowledge_dir: Path,
        output_dir: Path,
        exclude_patterns: Optional[List[str]] = None,
        exclude_regex: Optional[List[str]] = None,
        use_ai: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            pre_knowledge_dir: pre-knowledge/フォルダのパス
            output_dir: 出力先（knowledge/）フォルダのパス
            exclude_patterns: 除外するドキュメント名のリスト
            exclude_regex: 除外する正規表現パターンのリスト
            use_ai: AIを使用するかどうか（デフォルト: True）
            logger: ロガー
        """
        self.pre_knowledge_dir = Path(pre_knowledge_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.exclude_patterns = exclude_patterns or []
        self.exclude_regex = [re.compile(p) for p in (exclude_regex or [])]
        self.use_ai = use_ai
        self.logger = logger or logging.getLogger(__name__)
        
        # 既存ナレッジの情報
        self.existing_mapping: Dict = {}
        self.existing_structure: Dict = {}
        self.existing_files: Dict[str, Dict] = {}
        
        # OpenAIクライアント
        self._client = None
        self._model_name = None
        self._is_azure = False
        
        # 分析結果
        self._proposal: Optional[Proposal] = None
    
    def _get_openai_client(self):
        """Lazy-load OpenAI or Azure OpenAI client."""
        if self._client is None:
            try:
                from ..utils.openai_client import get_openai_client, get_model_name
                
                self._client, self._is_azure = get_openai_client(timeout=300.0, purpose="chat")
                self._model_name = get_model_name(purpose="chat", is_azure=self._is_azure)
                self.logger.info(f"ProposalGenerator using model: {self._model_name}")
                
            except Exception as e:
                self.logger.warning(f"Failed to initialize OpenAI client: {e}")
                self._client = None
        
        return self._client
    
    def _get_model_name(self) -> str:
        """Get model/deployment name for chat completions."""
        if self._model_name:
            return self._model_name
        from ..utils.openai_client import get_model_name
        return get_model_name(purpose="chat", is_azure=self._is_azure)
    
    def has_existing_knowledge(self) -> bool:
        """既存ナレッジがあるかチェック"""
        if not self.output_dir.exists():
            return False
        
        # mapping.json または *.md ファイルがあれば既存ナレッジあり
        mapping_path = self.output_dir / "mapping.json"
        if mapping_path.exists():
            return True
        
        md_files = list(self.output_dir.rglob("*.md"))
        return len(md_files) > 0
    
    def load_existing_knowledge(self) -> None:
        """既存ナレッジを読み込み・分析"""
        if not self.has_existing_knowledge():
            return
        
        # mapping.json を読み込み
        mapping_path = self.output_dir / "mapping.json"
        if mapping_path.exists():
            try:
                with open(mapping_path, "r", encoding="utf-8") as f:
                    self.existing_mapping = json.load(f)
            except Exception as e:
                self.logger.warning(f"Failed to load mapping.json: {e}")
        
        # 既存ファイルをスキャン
        self._scan_existing_files()
        
        # 構造を分析
        self._analyze_existing_structure()
    
    def _scan_existing_files(self) -> None:
        """既存ナレッジファイルをスキャン"""
        for md_file in self.output_dir.rglob("*.md"):
            # 特殊ファイルはスキップ
            if md_file.name in ["readme.md", "UPDATE_REPORT.md", "link_check_report.md"]:
                continue
            if md_file.name.startswith("00_"):
                continue
            
            rel_path = str(md_file.relative_to(self.output_dir))
            
            try:
                content = md_file.read_text(encoding="utf-8")
                
                self.existing_files[rel_path] = {
                    "path": rel_path,
                    "hash": self._compute_hash(content),
                    "char_count": len(content),
                    "summary": self._extract_summary(content),
                    "headings": self._extract_headings(content),
                    "category": md_file.parent.name if md_file.parent != self.output_dir else None,
                }
            except Exception as e:
                self.logger.warning(f"Failed to read {md_file}: {e}")
    
    def _analyze_existing_structure(self) -> None:
        """既存ナレッジの構造を分析"""
        categories = {}
        
        for file_info in self.existing_files.values():
            cat = file_info.get("category")
            if cat:
                if cat not in categories:
                    categories[cat] = {
                        "name": cat,
                        "file_count": 0,
                        "total_chars": 0,
                        "files": [],
                    }
                categories[cat]["file_count"] += 1
                categories[cat]["total_chars"] += file_info["char_count"]
                categories[cat]["files"].append(file_info["path"])
        
        self.existing_structure = {
            "categories": categories,
            "total_files": len(self.existing_files),
            "total_categories": len(categories),
        }
    
    def _compute_hash(self, content: str) -> str:
        """コンテンツのハッシュを計算"""
        return hashlib.md5(content.encode("utf-8")).hexdigest()
    
    def _extract_summary(self, content: str) -> str:
        """サマリーセクションを抽出"""
        lines = content.split("\n")
        in_summary = False
        summary_lines = []
        
        for line in lines:
            if line.strip().lower().startswith("## サマリー") or line.strip().lower().startswith("## summary"):
                in_summary = True
                continue
            elif in_summary and line.strip().startswith("## "):
                break
            elif in_summary and line.strip():
                summary_lines.append(line.strip())
        
        return "\n".join(summary_lines[:10])  # 最大10行
    
    def _extract_headings(self, content: str) -> List[str]:
        """見出しを抽出"""
        headings = []
        for line in content.split("\n"):
            match = re.match(r"^(#{1,4})\s+(.+)$", line)
            if match:
                headings.append(match.group(2).strip())
        return headings
    
    def _matches_exclude_pattern(self, doc_name: str) -> bool:
        """除外パターンにマッチするかチェック"""
        # 完全一致
        if doc_name in self.exclude_patterns:
            return True
        
        # 部分一致
        for pattern in self.exclude_patterns:
            if pattern in doc_name:
                return True
        
        # 正規表現マッチ
        for regex in self.exclude_regex:
            if regex.search(doc_name):
                return True
        
        return False
    
    def _is_already_processed(self, doc_name: str) -> Optional[str]:
        """既にナレッジ化済みかチェック（mapping.jsonを参照）
        
        Args:
            doc_name: 新しいドキュメント名
            
        Returns:
            既にナレッジ化済みの場合は対応するナレッジパス、そうでなければNone
        """
        if not self.existing_mapping:
            return None
        
        # 形式1: mappingsリスト形式（新しい形式）
        mappings = self.existing_mapping.get("mappings", [])
        if isinstance(mappings, list):
            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue
                inputs = mapping.get("input", [])
                if isinstance(inputs, list):
                    for inp in inputs:
                        if isinstance(inp, dict):
                            # {"file": "ドキュメント名", ...} 形式
                            if inp.get("file") == doc_name:
                                output = mapping.get("output", {})
                                if isinstance(output, dict):
                                    return output.get("path", "")
                        elif isinstance(inp, str):
                            # 文字列形式
                            if inp == doc_name:
                                output = mapping.get("output", {})
                                if isinstance(output, dict):
                                    return output.get("path", "")
        
        # 形式2: ルートレベルにパスをキーとした形式（古い形式）
        for key, entry in self.existing_mapping.items():
            # メタデータキーはスキップ
            if key in ["version", "created_at", "updated_at", "model", "mappings", "categories", "history"]:
                continue
            if isinstance(entry, dict):
                inputs = entry.get("inputs", [])
                if isinstance(inputs, list) and doc_name in inputs:
                    return key
        
        return None
    
    def _list_new_documents(self) -> List[Dict]:
        """pre-knowledge内の新しいドキュメントを一覧化"""
        documents = []
        
        for item in sorted(self.pre_knowledge_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            
            formatted_dir = item / FOLDER_FORMATTED_MARKDOWN
            formatted_md = formatted_dir / FILE_ENHANCED_MD
            
            if not formatted_md.exists():
                continue
            
            try:
                content = formatted_md.read_text(encoding="utf-8")
                
                documents.append({
                    "name": item.name,
                    "path": str(formatted_md),
                    "hash": self._compute_hash(content),
                    "char_count": len(content),
                    "summary": self._extract_summary(content),
                    "headings": self._extract_headings(content),
                    "content": content,
                })
            except Exception as e:
                self.logger.warning(f"Failed to read {formatted_md}: {e}")
        
        return documents
    
    def _get_embedding_client(self) -> Optional[Any]:
        """Embedding用のAzure OpenAIクライアントを取得
        
        優先順位:
        1. Embedding専用設定 (AZURE_OPENAI_ENDPOINT_EMBEDDING等)
        2. 共通設定 (AZURE_OPENAI_ENDPOINT等)
        """
        from ..utils.openai_client import load_env
        load_env()
        
        try:
            # Embedding専用設定 → 共通設定の順でフォールバック
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT_EMBEDDING") or os.getenv("AZURE_OPENAI_ENDPOINT")
            api_key = os.getenv("AZURE_OPENAI_API_KEY_EMBEDDING") or os.getenv("AZURE_OPENAI_API_KEY")
            api_version = os.getenv("AZURE_OPENAI_API_VERSION_EMBEDDING") or os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
            
            if not endpoint or not api_key:
                self.logger.warning("Azure OpenAI設定がありません。AZURE_OPENAI_ENDPOINTとAZURE_OPENAI_API_KEYを設定してください。")
                return None
            
            from openai import AzureOpenAI
            client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
            )
            return client
        except Exception as e:
            self.logger.warning(f"Failed to get embedding client: {e}")
            return None
    
    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """テキストのEmbeddingを取得"""
        client = self._get_embedding_client()
        if client is None:
            return None
        
        try:
            # テキストを適切な長さに制限
            text = text[:8000]
            
            # Azure OpenAI Embedding
            deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_EMBEDDING", "text-embedding-3-large")
            response = client.embeddings.create(
                input=text,
                model=deployment,
            )
            
            return response.data[0].embedding
        except Exception as e:
            self.logger.warning(f"Failed to get embedding: {e}")
            return None
    
    def _load_embedding_cache(self) -> Dict[str, List[float]]:
        """Embeddingキャッシュを読み込み"""
        cache_path = self.output_dir / EMBEDDING_CACHE_FILE
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning(f"Failed to load embedding cache: {e}")
        return {}
    
    def _save_embedding_cache(self, cache: Dict[str, List[float]]) -> None:
        """Embeddingキャッシュを保存"""
        cache_path = self.output_dir / EMBEDDING_CACHE_FILE
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception as e:
            self.logger.warning(f"Failed to save embedding cache: {e}")
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """コサイン類似度を計算"""
        a = np.array(vec1)
        b = np.array(vec2)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    
    def _filter_by_embedding(self, new_doc: Dict, existing_files: List[Dict], top_k: int = EMBEDDING_TOP_K) -> List[Dict]:
        """Embeddingで事前フィルタリングし、類似度上位N件を返す
        
        Args:
            new_doc: 新しいドキュメント
            existing_files: 既存ファイル一覧
            top_k: 返す件数
            
        Returns:
            類似度上位N件の既存ファイル
        """
        if len(existing_files) <= top_k:
            return existing_files
        
        # Embeddingキャッシュを読み込み
        cache = self._load_embedding_cache()
        
        # 新しいドキュメントのEmbeddingを取得
        new_text = f"{new_doc['name']}\n{new_doc.get('summary', '')}"
        new_embedding = self._get_embedding(new_text)
        
        if new_embedding is None:
            self.logger.warning("Embedding取得に失敗。全ファイルをAI比較に使用します。")
            return existing_files[:top_k]
        
        # 各既存ファイルとの類似度を計算
        similarities = []
        for f in existing_files:
            file_path = f["path"]
            
            # キャッシュにあればそれを使う
            if file_path in cache:
                file_embedding = cache[file_path]
            else:
                # なければ生成してキャッシュ
                file_text = f"{f['path']}\n{f.get('summary', '')}"
                file_embedding = self._get_embedding(file_text)
                if file_embedding is not None:
                    cache[file_path] = file_embedding
            
            if file_embedding is not None:
                sim = self._cosine_similarity(new_embedding, file_embedding)
                similarities.append((f, sim))
            else:
                # Embedding取得失敗時は低い類似度を設定
                similarities.append((f, 0.0))
        
        # キャッシュを保存
        self._save_embedding_cache(cache)
        
        # 類似度でソートして上位N件を返す
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_files = [f for f, _ in similarities[:top_k]]
        
        self.logger.info(f"Embedding事前フィルタ: {len(existing_files)}件 → {len(top_files)}件")
        
        return top_files
    
    def _calculate_similarity_with_ai(self, new_doc: Dict, existing_files: List[Dict]) -> Tuple[Optional[Dict], float, str]:
        """AIを使って類似度を計算し、最も類似する既存ファイルを探す
        
        Embedding事前フィルタを使用して、既存ファイルが多い場合は
        類似度上位N件のみをAIで詳細比較する。
        
        Returns:
            (best_match, similarity, reason)
        """
        client = self._get_openai_client()
        if client is None:
            # AIが使えない場合は従来の方法にフォールバック
            return self._find_best_match_simple(new_doc)
        
        # Embedding事前フィルタを適用（既存ファイルが多い場合）
        filtered_files = self._filter_by_embedding(new_doc, existing_files)
        
        # 既存ファイルの情報をまとめる（サマリーを重視）
        existing_info = []
        for i, f in enumerate(filtered_files):
            existing_info.append(f"""
ファイル{i+1}: {f['path']}
サマリー: {f.get('summary', '')[:300]}
""")
        
        prompt = f"""以下の「新しいドキュメント」と「既存ナレッジファイル」を比較して、最も類似するファイルを特定してください。

【新しいドキュメント】
ファイル名: {new_doc['name']}
サマリー: {new_doc.get('summary', '')[:300]}

【既存ナレッジファイル一覧】
{''.join(existing_info)}

【回答形式】
JSON形式で回答してください：
{{
    "best_match_index": <最も類似するファイルの番号（1始まり）。類似するものがなければnull>,
    "similarity": <類似度（0.0-1.0の小数）。0.8以上なら更新、0.8未満は新規追加>,
    "reason_update": "<更新の場合の理由（80%以上の場合）。150文字以内。例: 既存『02_xxx.md』と類似。共通点: ○○、△△。>",
    "reason_create": "<新規追加の場合の理由（80%未満の場合）。150文字以内。配置先カテゴリを推奨する理由を記載。>",
    "suggested_category": "<新規追加の場合、配置を推奨するカテゴリ名>"
}}

【判断基準】
サマリーの内容が同じトピック・業務を扱っているかを最重視

【reason_updateの記載ルール】★150文字以内★
- 「既存『ファイル名』と類似。共通点: ○○、△△。」

【reason_createの記載ルール】★150文字以内★
- 「○○に関する内容のため、「カテゴリ名」への配置を推奨。」
- 既存に類似がない場合でも、どのカテゴリに配置するのが適切かを理由とともに記載

【記載例】
reason_update: 「既存『02_コンロ見積.md』と類似。共通点: サービス提供可否判断、ビルトインコンロ取扱条件。」
reason_create: 「IVR設定に関する内容のため、「01_IVR設計」カテゴリへの配置を推奨。」
"""
        
        try:
            response = client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {"role": "system", "content": "あなたはナレッジ管理の専門家です。ドキュメントの類似性を正確に判断し、詳細な理由を日本語で記載してください。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=1000,
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONを抽出（ネストにも対応）
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                result = json.loads(json_match.group())
                
                best_index = result.get("best_match_index")
                similarity = float(result.get("similarity", 0.0))
                
                # 類似度に応じて適切なreasonを選択
                if similarity >= self.SIMILARITY_THRESHOLD:
                    reason = result.get("reason_update", result.get("reason", ""))
                else:
                    reason = result.get("reason_create", result.get("reason", ""))
                    suggested_cat = result.get("suggested_category", "")
                    if suggested_cat and suggested_cat not in reason:
                        reason = f"「{suggested_cat}」カテゴリへの配置を推奨。{reason}"
                
                if best_index is not None and 1 <= best_index <= len(filtered_files):
                    return (filtered_files[best_index - 1], similarity, reason)
                else:
                    return (None, similarity, reason)
            
        except Exception as e:
            self.logger.warning(f"AI similarity calculation failed: {e}")
        
        # フォールバック
        return self._find_best_match_simple(new_doc)
    
    def _find_best_match_simple(self, new_doc: Dict) -> Tuple[Optional[Dict], float, str]:
        """従来の文字列比較による類似度計算（AIなし）"""
        best_match = None
        best_similarity = 0.0
        
        for existing in self.existing_files.values():
            similarity = self._calculate_similarity(new_doc, existing)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = existing
        
        if best_match and best_similarity >= self.SIMILARITY_THRESHOLD:
            # 80%以上：更新として理由を生成
            reason = f"既存『{best_match['path']}』と類似。同一トピックのため更新対象。（AI判定が利用できないため簡易判定）"
            return (best_match, best_similarity, reason)
        elif best_match and best_similarity > 0.1:
            # 80%未満：新規追加として配置理由を生成
            category = best_match.get("category", "")
            if category:
                reason = f"「{category}」カテゴリに関連する内容のため、同カテゴリへ配置を推奨。（AI判定が利用できないため簡易判定）"
            else:
                reason = "新規トピックとして追加。（AI判定が利用できないため簡易判定）"
            return (best_match, best_similarity, reason)
        
        return (None, 0.0, "新規トピックとして追加。既存ナレッジに該当なし。")
    
    def _calculate_similarity(self, new_doc: Dict, existing: Dict) -> float:
        """類似度を計算（文字列比較版）"""
        score = 0.0
        
        # 1. ファイル名の類似度（20%）
        name_sim = self._string_similarity(new_doc["name"], existing.get("path", "").split("/")[-1])
        score += name_sim * 0.20
        
        # 2. サマリーの類似度（40%）
        summary_sim = self._string_similarity(new_doc.get("summary", ""), existing.get("summary", ""))
        score += summary_sim * 0.40
        
        # 3. 見出しの一致度（30%）
        heading_sim = self._list_similarity(new_doc.get("headings", []), existing.get("headings", []))
        score += heading_sim * 0.30
        
        # 4. 文字数の近さ（10%）
        char_sim = 1.0 - min(abs(new_doc.get("char_count", 0) - existing.get("char_count", 0)) / max(new_doc.get("char_count", 1), existing.get("char_count", 1)), 1.0)
        score += char_sim * 0.10
        
        return score
    
    def _string_similarity(self, s1: str, s2: str) -> float:
        """文字列の類似度（簡易版）"""
        if not s1 or not s2:
            return 0.0
        
        # 共通する単語の割合
        words1 = set(re.findall(r"\w+", s1.lower()))
        words2 = set(re.findall(r"\w+", s2.lower()))
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1 & words2
        union = words1 | words2
        
        return len(intersection) / len(union) if union else 0.0
    
    def _list_similarity(self, list1: List[str], list2: List[str]) -> float:
        """リストの類似度"""
        if not list1 or not list2:
            return 0.0
        
        set1 = set(h.lower() for h in list1)
        set2 = set(h.lower() for h in list2)
        
        intersection = set1 & set2
        union = set1 | set2
        
        return len(intersection) / len(union) if union else 0.0
    
    def _suggest_path_with_ai(self, new_doc: Dict) -> Tuple[str, List[str]]:
        """AIを使って新規ファイルの配置先を提案
        
        Returns:
            (suggested_path, split_paths)
            split_paths: 分割する場合のパス一覧
        """
        client = self._get_openai_client()
        if client is None:
            return (self._suggest_path_simple(new_doc), [])
        
        # 既存カテゴリの情報（次のファイル番号付き）
        categories_info = []
        for cat_name, cat_info in self.existing_structure.get("categories", {}).items():
            next_num = self._peek_next_file_number(cat_name)
            categories_info.append(f"- {cat_name}: {cat_info['file_count']}ファイル、次の番号は{next_num:02d}")
        
        # 分割が必要かどうかの情報
        needs_split = new_doc.get("char_count", 0) > self.LARGE_FILE_THRESHOLD
        chapter_count = len(new_doc.get("headings", []))
        
        prompt = f"""新しいドキュメントを既存のナレッジベースに追加します。
最適な配置先カテゴリとファイル名を提案してください。

【新しいドキュメント】
ファイル名: {new_doc['name']}
サマリー: {new_doc.get('summary', '')[:300]}
主な見出し: {', '.join(new_doc.get('headings', [])[:10])}
文字数: {new_doc.get('char_count', 0)}
章数: {chapter_count}

【既存カテゴリと次に使用可能なファイル番号】★必ずこの番号から開始★
{chr(10).join(categories_info) if categories_info else '（なし - 新規カテゴリを作成してください）'}

【分割について】
- ファイルサイズ: {new_doc.get('char_count', 0)}文字
- 分割推奨閾値: {self.LARGE_FILE_THRESHOLD}文字
- 分割が必要か: {"はい（大きいファイルなので分割推奨）" if needs_split else "いいえ"}

【回答形式】
JSON形式で回答してください：
{{
    "category": "<既存カテゴリ名 or 新規カテゴリ名>",
    "filename": "<ファイル名（分割しない場合のみ使用。番号なしでOK、例: 入電予測.md）>",
    "should_split": <true/false>,
    "split_files": [
        "<分割後のファイル名1（番号なしでOK、例: 入電予測_概要.md）>",
        "<分割後のファイル名2>"
    ],
    "reason": "<配置理由>"
}}

【★重要なルール★】
1. ファイル番号は必ず「次に使用可能なファイル番号」から開始
   例: 次の番号が03なら → 03_xxx.md, 04_xxx.md, 05_xxx.md
2. 分割する場合:
   - 連番を使用（例: 03_, 04_, 05_）
   - 共通のベース名 + サフィックス（例: 03_入電予測_概要.md, 04_入電予測_詳細.md）
3. 分割しない場合: filenameに1つのファイル名を記載
4. ファイル名は内容を反映した簡潔な日本語名に
5. 日付（202512等）や記号（【】★等）は含めない
6. 5万文字以上のファイルは分割を推奨
"""
        
        try:
            response = client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {"role": "system", "content": "あなたはナレッジ管理の専門家です。ドキュメントの分類と整理を行ってください。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=500,
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONを抽出
            json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                
                category = result.get("category", "01_新規")
                should_split = result.get("should_split", False)
                split_files = result.get("split_files", [])
                
                if should_split and split_files:
                    # 分割する場合: ファイル番号を強制的に正しい連番に修正
                    corrected_split_files = []
                    for i, f in enumerate(split_files):
                        next_num = self._get_next_file_number(category)
                        # ファイル名から番号部分を除去して、正しい番号を付与
                        clean_name = re.sub(r'^\d+_', '', f)
                        corrected_name = f"{next_num:02d}_{clean_name}"
                        corrected_split_files.append(corrected_name)
                    
                    split_paths = [f"{category}/{f}" for f in corrected_split_files]
                    # 分割時はメインパスは最初の分割ファイル
                    main_path = split_paths[0] if split_paths else f"{category}/document.md"
                    return (main_path, split_paths)
                else:
                    # 分割しない場合
                    next_num = self._get_next_file_number(category)
                    filename = result.get("filename", "document.md")
                    # ファイル名から番号部分を除去して、正しい番号を付与
                    clean_name = re.sub(r'^\d+_', '', filename)
                    corrected_filename = f"{next_num:02d}_{clean_name}"
                    main_path = f"{category}/{corrected_filename}"
                    return (main_path, [])
            
        except Exception as e:
            self.logger.warning(f"AI path suggestion failed: {e}")
        
        # フォールバック
        return (self._suggest_path_simple(new_doc), [])
    
    def _suggest_path_simple(self, new_doc: Dict) -> str:
        """従来のパス提案（AIなし）"""
        if self.existing_structure.get("categories"):
            # 既存カテゴリの最初に追加
            first_category = list(self.existing_structure["categories"].keys())[0]
            
            # 次のファイル番号を取得
            next_number = self._get_next_file_number(first_category)
            new_filename = f"{next_number:02d}_{self._clean_filename(new_doc['name'])}.md"
            
            return f"{first_category}/{new_filename}"
        else:
            # 新規カテゴリ
            return f"01_新規/{self._clean_filename(new_doc['name'])}.md"
    
    def _init_category_counter(self, category: str) -> None:
        """カテゴリのファイル番号カウンタを初期化"""
        if not hasattr(self, '_pending_file_numbers'):
            self._pending_file_numbers = {}
        
        if category not in self._pending_file_numbers:
            max_existing = 0
            if self.existing_structure.get("categories", {}).get(category):
                files = self.existing_structure["categories"][category].get("files", [])
                for f in files:
                    # パスからファイル名だけを抽出（Windows/Unix両対応）
                    # 例: 01_xxx\07_yyy.md または 01_xxx/07_yyy.md → 07_yyy.md
                    filename = Path(f).name
                    # ファイル名から番号を抽出（例: 07_概要.md → 7）
                    match = re.match(r'^(\d+)_', filename)
                    if match:
                        num = int(match.group(1))
                        max_existing = max(max_existing, num)
                    self.logger.debug(f"  File: {f} -> filename: {filename}, num: {match.group(1) if match else 'N/A'}")
            self._pending_file_numbers[category] = max_existing
            self.logger.info(f"Category '{category}' max file number: {max_existing}, next: {max_existing + 1}")
    
    def _peek_next_file_number(self, category: str) -> int:
        """カテゴリの次のファイル番号を確認（カウンタは更新しない）
        
        Args:
            category: カテゴリ名
        
        Returns:
            次のファイル番号
        """
        self._init_category_counter(category)
        return self._pending_file_numbers[category] + 1
    
    def _get_next_file_number(self, category: str) -> int:
        """カテゴリの次のファイル番号を取得して使用（カウンタを更新）
        
        Args:
            category: カテゴリ名
        
        Returns:
            次のファイル番号
        """
        self._init_category_counter(category)
        
        # 次の番号を返してカウンタを更新
        self._pending_file_numbers[category] += 1
        return self._pending_file_numbers[category]
    
    def _consume_file_numbers(self, category: str, count: int) -> None:
        """カテゴリのファイル番号を消費（複数ファイル追加時に使用）
        
        Args:
            category: カテゴリ名
            count: 消費する番号の数
        """
        self._init_category_counter(category)
        self._pending_file_numbers[category] += count
    
    def _clean_filename(self, name: str) -> str:
        """ファイル名をクリーンアップ"""
        # 日付パターンを削除
        name = re.sub(r"【[^】]*】", "", name)
        name = re.sub(r"\d{4}[-_]?\d{2}[-_]?\d{2}", "", name)
        name = re.sub(r"[★☆●○◆◇■□▲△▼▽]", "", name)
        name = re.sub(r"_+", "_", name)
        name = name.strip("_- ")
        
        return name if name else "document"
    
    def _truncate(self, text: str, max_len: int) -> str:
        """テキストを指定長で切り詰め"""
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."
    
    def _determine_action(self, new_doc: Dict) -> ProposedAction:
        """アクション（新規追加/更新/スキップ）を判定"""
        
        # 1. 除外チェック
        if self._matches_exclude_pattern(new_doc["name"]):
            return ProposedAction(
                action_type=ActionType.SKIP,
                source_document=new_doc["name"],
                target_path=None,
                reason="除外設定にマッチ",
            )
        
        # 2. 既にナレッジ化済みかチェック（mapping.jsonを参照）
        already_processed = self._is_already_processed(new_doc["name"])
        if already_processed:
            target_path = already_processed  # 既存のナレッジパス
            return ProposedAction(
                action_type=ActionType.SKIP,
                source_document=new_doc["name"],
                target_path=target_path,
                reason="既にナレッジ化済み（mapping.jsonに存在）",
            )
        
        # 3. 既存ナレッジと照合（AI使用）
        if self.use_ai and self.existing_files:
            existing_list = list(self.existing_files.values())
            best_match, similarity, reason = self._calculate_similarity_with_ai(new_doc, existing_list)
        else:
            result = self._find_best_match_simple(new_doc)
            best_match, similarity, reason = result
        
        if best_match is None:
            # 該当なし → 新規追加（AIで配置先を提案）
            if self.use_ai:
                target_path, split_paths = self._suggest_path_with_ai(new_doc)
            else:
                target_path = self._suggest_path_simple(new_doc)
                split_paths = []
            
            return ProposedAction(
                action_type=ActionType.CREATE,
                source_document=new_doc["name"],
                target_path=target_path,
                reason=reason or "既存に該当なし",
                similarity=similarity,
                split_into=split_paths,
            )
        
        # 3. ハッシュ比較（完全一致）
        if best_match["hash"] == new_doc["hash"]:
            return ProposedAction(
                action_type=ActionType.SKIP,
                source_document=new_doc["name"],
                target_path=best_match["path"],
                reason="変更なし（ハッシュ一致）",
                similarity=1.0,
            )
        
        # 4. 類似度判定（80%を境界にシンプルに2段階）
        if similarity < self.SIMILARITY_THRESHOLD:
            # 80%未満 → 新規作成
            if self.use_ai:
                target_path, split_paths = self._suggest_path_with_ai(new_doc)
            else:
                target_path = self._suggest_path_simple(new_doc)
                split_paths = []
            
            # 配置先カテゴリを取得して理由を補完
            category = target_path.split("/")[0] if "/" in target_path else "新規カテゴリ"
            if not reason:
                create_reason = f"新規追加。「{category}」カテゴリへ配置（類似度{similarity*100:.0f}%）"
            else:
                # reasonに類似度情報を追加（まだない場合）
                if "類似度" not in reason and "%" not in reason:
                    create_reason = f"{reason}（類似度{similarity*100:.0f}%）"
                else:
                    create_reason = reason
            
            return ProposedAction(
                action_type=ActionType.CREATE,
                source_document=new_doc["name"],
                target_path=target_path,
                reason=create_reason,
                similarity=similarity,
                split_into=split_paths,
            )
        
        # 5. 80%以上 → Stage 2: セクション単位で複数ファイルへの配置を判定
        multi_target = self._determine_multi_target_placements(new_doc, best_match, existing_list)
        
        # 変更詳細も生成（後方互換性のため）
        changes = self._detect_changes_with_line_numbers(new_doc, best_match)
        
        return ProposedAction(
            action_type=ActionType.UPDATE,
            source_document=new_doc["name"],
            target_path=best_match["path"],  # メインの更新先
            reason=reason or f"更新（類似度{similarity*100:.0f}% ≥ 80%）",
            similarity=similarity,
            changes=changes,
            multi_target=multi_target,
        )
    
    def _determine_multi_target_placements(
        self, 
        new_doc: Dict, 
        best_match: Dict,
        existing_list: List[Dict]
    ) -> MultiTargetUpdate:
        """Stage 2: セクション単位で複数ファイルへの配置先を判定
        
        Args:
            new_doc: 新しいドキュメント
            best_match: 最も類似するファイル（メインの更新先）
            existing_list: 既存ファイル一覧
            
        Returns:
            MultiTargetUpdate: 複数ファイルへの更新情報
        """
        multi_target = MultiTargetUpdate()
        
        # 新規ドキュメントの内容を取得
        new_content = new_doc.get("content", "")
        if not new_content:
            try:
                new_path = Path(new_doc.get("path", ""))
                if new_path.exists():
                    new_content = new_path.read_text(encoding="utf-8")
            except Exception:
                pass
        
        if not new_content:
            return multi_target
        
        # セクションを抽出
        new_sections = self._extract_sections_with_lines(new_content)
        multi_target.total_sections = len(new_sections)
        
        if len(new_sections) == 0:
            return multi_target
        
        # メインターゲットファイル（類似度が最も高いファイル）のセクション情報を取得
        main_path = best_match["path"]
        main_file_sections = {}  # heading -> {lines, content}
        
        try:
            main_full_path = self.output_dir / main_path
            if main_full_path.exists():
                main_content = main_full_path.read_text(encoding="utf-8")
                main_file_sections = self._extract_sections_with_lines(main_content)
        except Exception as e:
            self.logger.warning(f"Could not read main target file {main_path}: {e}")
        
        # 各セクションの配置先を判定（メインファイルに対してのみ比較）
        # セクション名ではなく、セクション内容の類似度で比較
        target_files = {}  # file_path -> TargetFileInfo
        unchanged_sections = []  # 変更なしのセクション
        
        # メインファイルのTargetFileInfoを初期化
        target_files[main_path] = TargetFileInfo(
            file_path=main_path,
            action="update",
        )
        
        # 既存セクションの内容リストを準備（類似度検索用）
        existing_section_list = []
        for ex_heading, ex_info in main_file_sections.items():
            existing_section_list.append({
                "heading": ex_heading,
                "content": ex_info.get("content", ""),
                "lines": ex_info.get("lines", (0, 0)),
            })
        
        # 使用済みの既存セクション（同じセクションを複数回更新しないため）
        used_existing_sections = set()
        
        for heading, section_info in new_sections.items():
            new_section_content = section_info.get("content", "").strip()
            new_content_normalized = self._normalize_content(new_section_content)
            
            # 1. まず完全一致をチェック
            if heading in main_file_sections:
                existing_info = main_file_sections[heading]
                existing_content = existing_info.get("content", "").strip()
                existing_content_normalized = self._normalize_content(existing_content)
                
                line_info = f"{existing_info.get('lines', (0, 0))[0]}-{existing_info.get('lines', (0, 0))[1]}行"
                
                if new_content_normalized != existing_content_normalized:
                    target_files[main_path].update_sections.append(heading)
                    target_files[main_path].update_lines[heading] = line_info
                else:
                    unchanged_sections.append(heading)
                    self.logger.debug(f"Section '{heading}' unchanged (content identical), skipping")
                
                used_existing_sections.add(heading)
                continue
            
            # 2. 完全一致しない場合、セクション内容で類似度を計算
            best_match_section = None
            best_similarity = 0.0
            
            for ex_section in existing_section_list:
                ex_heading = ex_section["heading"]
                
                # 既に使用済みのセクションはスキップ
                if ex_heading in used_existing_sections:
                    continue
                
                ex_content = ex_section["content"].strip()
                ex_content_normalized = self._normalize_content(ex_content)
                
                # 内容ベースの類似度を計算
                similarity = self._calculate_content_similarity(new_section_content, ex_content)
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match_section = ex_section
            
            # 類似度が閾値（50%）以上なら既存セクションの更新として扱う
            SECTION_SIMILARITY_THRESHOLD = 0.50
            
            if best_match_section and best_similarity >= SECTION_SIMILARITY_THRESHOLD:
                # 類似するセクションが見つかった → 更新
                ex_heading = best_match_section["heading"]
                line_info = f"{best_match_section['lines'][0]}-{best_match_section['lines'][1]}行"
                
                # 更新として記録（新規セクション名 → 既存セクション を更新）
                update_label = f"{heading} → {ex_heading}"
                target_files[main_path].update_sections.append(update_label)
                target_files[main_path].update_lines[update_label] = f"{line_info} (類似度{best_similarity*100:.0f}%)"
                
                used_existing_sections.add(ex_heading)
                self.logger.debug(f"Section '{heading}' matched to '{ex_heading}' (similarity: {best_similarity:.2f})")
            else:
                # 類似するセクションがない → 新規追加
                target_files[main_path].new_sections.append(heading)
        
        # 変更なしセクションをMultiTargetUpdateに保存
        multi_target.unchanged_sections = unchanged_sections
        
        # 変更なしセクション数をログ出力
        if unchanged_sections:
            self.logger.info(f"Unchanged sections (content identical): {len(unchanged_sections)} sections")
        
        # TargetFileInfoのリストを作成
        for file_path, info in target_files.items():
            multi_target.targets.append(info)
        
        return multi_target
    
    def _detect_changes(self, new_doc: Dict, existing: Dict) -> List[ChangeDetail]:
        """変更箇所を検出"""
        changes = []
        
        # 新しい見出しと既存の見出しを比較
        new_headings = set(new_doc.get("headings", []))
        existing_headings = set(existing.get("headings", []))
        
        # 追加された見出し（新規セクション）
        added = new_headings - existing_headings
        for heading in list(added)[:15]:  # 最大15件表示
            changes.append(ChangeDetail(
                item=f"➕ {heading}",
                target_file=existing["path"],
                target_line="（新規追加）",
                before_content="（なし）",
                after_content=f"新規追加",
                is_primary=True,
            ))
        if len(added) > 15:
            changes.append(ChangeDetail(
                item=f"➕ ... 他{len(added) - 15}件の新規セクション",
                target_file=existing["path"],
                target_line="",
                before_content="",
                after_content="",
                is_primary=True,
            ))
        
        # 共通の見出し（更新対象セクション）→ 主要変更として扱う
        common = new_headings & existing_headings
        for heading in list(common)[:10]:  # 最大10件表示
            changes.append(ChangeDetail(
                item=f"📝 {heading}",
                target_file=existing["path"],
                target_line="（既存セクション更新）",
                before_content="既存の内容",
                after_content="更新された内容",
                is_primary=True,  # 内容更新は主要変更
            ))
        if len(common) > 10:
            changes.append(ChangeDetail(
                item=f"📝 ... 他{len(common) - 10}件の既存セクション更新",
                target_file=existing["path"],
                target_line="",
                before_content="",
                after_content="",
                is_primary=True,  # 内容更新は主要変更
            ))
        
        return changes
    
    def _detect_changes_with_line_numbers(self, new_doc: Dict, existing: Dict) -> List[ChangeDetail]:
        """変更箇所を行番号付きで検出
        
        セクション単位で既存ファイルと比較し、
        - 更新セクション: 既存の何行目にあるか、内容が変更されるか
        - 新規セクション: 末尾に追加される
        を記録する
        """
        changes = []
        
        # 既存ファイルの内容を読み込み
        existing_content = ""
        try:
            full_path = self.output_dir / existing.get("path", "")
            if full_path.exists():
                existing_content = full_path.read_text(encoding="utf-8")
        except Exception:
            pass
        
        # 新規ドキュメントの内容を取得
        new_content = new_doc.get("content", "")
        if not new_content:
            try:
                new_path = Path(new_doc.get("path", ""))
                if new_path.exists():
                    new_content = new_path.read_text(encoding="utf-8")
            except Exception:
                pass
        
        # 既存ファイルのセクションを抽出（見出し → (行番号, 内容)）
        existing_sections = self._extract_sections_with_lines(existing_content)
        
        # 新規ドキュメントのセクションを抽出
        new_sections = self._extract_sections_with_lines(new_content)
        
        # セクション名の集合
        existing_headings = set(existing_sections.keys())
        new_headings = set(new_sections.keys())
        
        # 1. 新規セクション（既存にない見出し）
        added = new_headings - existing_headings
        def sort_key(h):
            match = re.match(r'^(\d+)', h)
            if match:
                return (0, int(match.group(1)), h)
            return (1, 0, h)
        sorted_added = sorted(added, key=sort_key)
        
        for heading in sorted_added[:15]:
            changes.append(ChangeDetail(
                item=f"✨ 新規: {heading}",
                target_file=existing["path"],
                target_line="（末尾に追加）",
                before_content="（なし）",
                after_content="新規セクション追加",
                is_primary=True,
            ))
        if len(added) > 15:
            changes.append(ChangeDetail(
                item=f"✨ ... 他{len(added) - 15}件の新規セクション",
                target_file=existing["path"],
                target_line="",
                before_content="",
                after_content="",
                is_primary=True,
            ))
        
        # 2. 共通の見出し（内容が異なるものは更新、同じものはスキップ）
        common = new_headings & existing_headings
        updated_sections = []
        unchanged_sections = []
        
        for heading in common:
            existing_info = existing_sections.get(heading, {})
            new_info = new_sections.get(heading, {})
            
            existing_text = existing_info.get("content", "").strip()
            new_text = new_info.get("content", "").strip()
            
            # 内容が異なるかチェック（空白を正規化して比較）
            if self._normalize_content(existing_text) != self._normalize_content(new_text):
                updated_sections.append((heading, existing_info))
            else:
                unchanged_sections.append(heading)
        
        # 更新されるセクション（内容が変更されるもの）→ 主要変更
        for heading, info in updated_sections[:10]:
            line_info = info.get("lines", (0, 0))
            if line_info[0] > 0:
                line_str = f"{line_info[0]}-{line_info[1]}行"
            else:
                line_str = "（位置不明）"
            
            changes.append(ChangeDetail(
                item=f"📝 更新: {heading}",
                target_file=existing["path"],
                target_line=line_str,
                before_content="既存の内容",
                after_content="更新された内容",
                is_primary=True,  # 内容更新は主要変更
            ))
        if len(updated_sections) > 10:
            changes.append(ChangeDetail(
                item=f"📝 ... 他{len(updated_sections) - 10}件の更新セクション",
                target_file=existing["path"],
                target_line="",
                before_content="",
                after_content="",
                is_primary=True,  # 内容更新は主要変更
            ))
        
        # 3. 既存ファイルにあって新規にない見出し（マージ時に保持される）
        removed = existing_headings - new_headings
        if removed:
            # 特殊セクション（サマリー、目次など）は除外
            special_sections = {"サマリー", "目次", "概要", "Summary"}
            removed = removed - special_sections
            if removed:
                changes.append(ChangeDetail(
                    item=f"📌 保持: 既存{len(removed)}セクション",
                    target_file=existing["path"],
                    target_line="（既存内容を保持）",
                    before_content="",
                    after_content="",
                    is_primary=False,
                ))
        
        return changes
    
    def _extract_sections_with_lines(self, content: str) -> Dict[str, Dict]:
        """コンテンツをセクション単位で分割し、行番号情報も含める
        
        Returns:
            {heading: {"content": str, "lines": (start, end)}, ...}
        """
        sections = {}
        if not content:
            return sections
        
        lines = content.split("\n")
        current_heading = "（冒頭）"
        current_start = 1
        current_content = []
        
        for i, line in enumerate(lines, 1):
            match = re.match(r"^(#{1,4})\s+(.+)$", line)
            if match:
                # 前のセクションを保存
                if current_content or current_heading != "（冒頭）":
                    sections[current_heading] = {
                        "content": "\n".join(current_content),
                        "lines": (current_start, i - 1),
                    }
                current_heading = match.group(2).strip()
                current_start = i
                current_content = []
            else:
                current_content.append(line)
        
        # 最後のセクションを保存
        if current_content or current_heading != "（冒頭）":
            sections[current_heading] = {
                "content": "\n".join(current_content),
                "lines": (current_start, len(lines)),
            }
        
        return sections
    
    def _normalize_content(self, text: str) -> str:
        """コンテンツを正規化（比較用）
        
        空白、改行、インデントの違いを無視して比較できるようにする
        """
        if not text:
            return ""
        # 連続する空白を1つに
        text = re.sub(r'\s+', ' ', text)
        # 前後の空白を削除
        text = text.strip()
        return text
    
    def _calculate_content_similarity(self, text1: str, text2: str) -> float:
        """2つのテキストの類似度を計算（0.0〜1.0）
        
        Jaccard類似度とn-gram類似度の組み合わせで計算
        """
        if not text1 or not text2:
            return 0.0
        
        # 正規化
        text1 = self._normalize_content(text1)
        text2 = self._normalize_content(text2)
        
        if text1 == text2:
            return 1.0
        
        # 1. 単語ベースのJaccard類似度
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        jaccard = intersection / union if union > 0 else 0.0
        
        # 2. 3-gramベースの類似度
        def get_ngrams(text: str, n: int = 3) -> set:
            return set(text[i:i+n] for i in range(len(text) - n + 1))
        
        ngrams1 = get_ngrams(text1.lower())
        ngrams2 = get_ngrams(text2.lower())
        
        if not ngrams1 or not ngrams2:
            return jaccard
        
        ngram_intersection = len(ngrams1 & ngrams2)
        ngram_union = len(ngrams1 | ngrams2)
        ngram_sim = ngram_intersection / ngram_union if ngram_union > 0 else 0.0
        
        # 3. 重み付け平均（Jaccard 40%, n-gram 60%）
        similarity = 0.4 * jaccard + 0.6 * ngram_sim
        
        return similarity
    
    def _extract_sections(self, content: str) -> List[Dict[str, Any]]:
        """ドキュメントをセクション単位で分割
        
        Returns:
            セクション情報のリスト: [{"heading": str, "content": str, "char_count": int}, ...]
        """
        sections = []
        lines = content.split("\n")
        current_heading = "（冒頭）"
        current_content = []
        
        for line in lines:
            # 見出し行を検出（##, ###, ####）
            match = re.match(r"^(#{1,4})\s+(.+)$", line)
            if match:
                # 前のセクションを保存
                if current_content:
                    content_text = "\n".join(current_content)
                    sections.append({
                        "heading": current_heading,
                        "content": content_text,
                        "char_count": len(content_text),
                    })
                current_heading = match.group(2).strip()
                current_content = []
            else:
                current_content.append(line)
        
        # 最後のセクションを保存
        if current_content:
            content_text = "\n".join(current_content)
            sections.append({
                "heading": current_heading,
                "content": content_text,
                "char_count": len(content_text),
            })
        
        return sections
    
    def _analyze_sections_for_partial_update(
        self, 
        new_doc: Dict, 
        best_match: Dict,
        existing_list: List[Dict]
    ) -> Optional[PartialUpdateInfo]:
        """セクション単位で類似度を分析し、部分更新情報を生成
        
        Args:
            new_doc: 新しいドキュメント
            best_match: 最も類似する既存ドキュメント
            existing_list: 既存ドキュメントのリスト
            
        Returns:
            部分更新情報（部分更新が適切な場合）、None（全体更新/新規が適切な場合）
        """
        # 新しいドキュメントの内容を取得
        new_content = new_doc.get("content", "")
        if not new_content:
            formatted_path = new_doc.get("formatted_path")
            if formatted_path and Path(formatted_path).exists():
                new_content = Path(formatted_path).read_text(encoding="utf-8")
        
        if not new_content or len(new_content) < 2000:
            # 小さいドキュメントは部分更新不要
            return None
        
        # セクションに分割
        sections = self._extract_sections(new_content)
        if len(sections) < 2:
            return None
        
        # 既存ドキュメントの見出しセットを作成
        existing_headings = set(best_match.get("headings", []))
        
        # セクションを「更新対象」と「新規作成対象」に分類
        update_sections = []
        create_sections = []
        update_char_count = 0
        create_char_count = 0
        
        for section in sections:
            heading = section["heading"]
            char_count = section["char_count"]
            
            # 既存の見出しと一致するか判定
            if heading in existing_headings or heading == "（冒頭）":
                update_sections.append(heading)
                update_char_count += char_count
            else:
                # 部分一致も検索
                is_similar = False
                for existing_heading in existing_headings:
                    # 単純な部分一致チェック
                    if heading in existing_heading or existing_heading in heading:
                        is_similar = True
                        break
                
                if is_similar:
                    update_sections.append(heading)
                    update_char_count += char_count
                else:
                    create_sections.append(heading)
                    create_char_count += char_count
        
        # 部分更新が有効な条件を判定
        # - 新規作成部分が30%以上ある
        # - 更新部分も20%以上ある
        total_chars = update_char_count + create_char_count
        if total_chars == 0:
            return None
        
        update_ratio = update_char_count / total_chars
        create_ratio = create_char_count / total_chars
        
        if create_ratio < 0.30 or update_ratio < 0.20:
            # 条件を満たさない場合は部分更新しない
            return None
        
        # 新規作成先のパスを提案
        if self.use_ai:
            create_target, _ = self._suggest_path_with_ai(new_doc)
        else:
            create_target = self._suggest_path_simple(new_doc)
        
        return PartialUpdateInfo(
            update_sections=update_sections[:10],  # 最大10件表示
            update_target=best_match["path"],
            update_char_count=update_char_count,
            create_sections=create_sections[:10],  # 最大10件表示
            create_target=create_target,
            create_char_count=create_char_count,
        )
    
    def _check_needs_restructure(self, new_documents: List[Dict]) -> Tuple[bool, str]:
        """AIを使って抜本的変更が必要かチェック
        
        既存のナレッジ構成と新規ファイルの内容を見て、
        構成を変えた方がよいかをAIが判断する。
        """
        if not self.use_ai:
            return False, ""
        
        client = self._get_openai_client()
        if client is None:
            return False, ""
        
        # 既存ナレッジの構成情報
        existing_categories = list(self.existing_structure.get("categories", {}).keys())
        existing_files_info = []
        for path, info in list(self.existing_files.items())[:20]:
            existing_files_info.append(f"- {path}: {info.get('summary', '')[:100]}")
        
        # 新規ファイルの情報
        new_files_info = []
        for doc in new_documents[:10]:
            new_files_info.append(f"- {doc['name']}: {doc.get('summary', '')[:100]}")
        
        prompt = f"""既存のナレッジ構成と新規ファイルを比較して、「抜本的な構成変更」が必要かを判断してください。

【既存ナレッジ（knowledge/）の構成】
カテゴリ: {', '.join(existing_categories) if existing_categories else 'なし'}

ファイル例:
{chr(10).join(existing_files_info) if existing_files_info else 'なし'}

【新規ファイル】
{chr(10).join(new_files_info) if new_files_info else 'なし'}

【判断基準】
■ 抜本的変更が必要なケース（既存フォルダ構造の変更が必要）:
- 既存カテゴリを分割する必要がある（例: 01_業務 → 01_申込 + 02_審査）
- 既存カテゴリを統合・再編する必要がある
- 既存ファイルを別カテゴリに移動する必要がある
- 既存の分類体系自体が不適切で、全体を再構成すべき

■ 抜本的変更が不要なケース（増分更新で対応可能）:
- 新規カテゴリを追加するだけ（例: 03_新機能/ を新設）
- 既存カテゴリにファイルを追加・更新するだけ
- 既存フォルダ構造を維持したまま対応できる

【回答形式】
JSON形式で回答してください：
{{
    "needs_restructure": <true/false>,
    "reason": "<判断理由（日本語で簡潔に）>"
}}
"""
        
        try:
            response = client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {"role": "system", "content": "あなたはナレッジ管理の専門家です。構成の適切さを判断してください。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=300,
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONを抽出
            json_match = re.search(r'\{[^{}]*\}', result_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                needs = result.get("needs_restructure", False)
                reason = result.get("reason", "")
                return (needs, reason)
            
        except Exception as e:
            self.logger.warning(f"AI restructure check failed: {e}")
        
        return False, ""
    
    def generate(self) -> Proposal:
        """提案を生成"""
        # 既存ナレッジを読み込み
        self.load_existing_knowledge()
        
        # 新しいドキュメントを一覧化（モード判定に使用）
        new_documents = self._list_new_documents()
        
        # モード判定
        if not self.has_existing_knowledge():
            mode = UpdateMode.NEW
            restructure_reason = ""
        else:
            # AIが構成の適切さを判断
            needs_restructure, restructure_reason = self._check_needs_restructure(new_documents)
            if needs_restructure:
                mode = UpdateMode.RESTRUCTURE
            else:
                mode = UpdateMode.INCREMENTAL
        
        # カテゴリごとの追加済みファイル番号を管理
        self._pending_file_numbers = {}
        
        # 各ドキュメントのアクションを判定
        actions = []
        excluded = []
        
        for doc in new_documents:
            action = self._determine_action(doc)
            
            if action.action_type == ActionType.SKIP and "除外設定" in action.reason:
                excluded.append(doc["name"])
            else:
                actions.append(action)
        
        # サマリー統計（80%境界でシンプルに2種類）
        summary = {
            "create": len([a for a in actions if a.action_type == ActionType.CREATE]),
            "update": len([a for a in actions if a.action_type in (ActionType.UPDATE, ActionType.PARTIAL_UPDATE)]),
            "skip": len([a for a in actions if a.action_type == ActionType.SKIP]),
        }
        
        self._proposal = Proposal(
            mode=mode,
            actions=actions,
            excluded_documents=excluded,
            summary=summary,
            needs_restructure=(mode == UpdateMode.RESTRUCTURE),
            restructure_reason=restructure_reason,
        )
        
        return self._proposal
    
    def regenerate_with_instruction(self, user_instruction: str) -> Proposal:
        """ユーザーの指示に基づいて提案を再生成
        
        Args:
            user_instruction: ユーザーからの指示
                例: 「コンロ見積の資料は02_契約に配置」
                例: 「業務設計資料は更新ではなく新規作成にして」
                例: 「FAQはスキップして」
        
        Returns:
            再生成された提案
        """
        if not self._proposal:
            return self.generate()
        
        client = self._get_openai_client()
        if client is None:
            return self._proposal
        
        # 既存カテゴリの情報
        existing_categories = list(self.existing_structure.get("categories", {}).keys())
        
        # 現在の提案内容
        current_actions = []
        for action in self._proposal.actions:
            current_actions.append(f"- {action.source_document} → {action.target_path} (action={action.action_type.value})")
        
        prompt = f"""ユーザーの指示に基づいて、ナレッジの更新提案を修正してください。

【既存カテゴリ】
{chr(10).join(existing_categories) if existing_categories else 'なし'}

【現在の提案】
{chr(10).join(current_actions)}

【ユーザーの指示】
{user_instruction}

【回答形式】
JSON形式で回答してください：
{{
    "actions": [
        {{
            "source": "<入力資料名>",
            "action_type": "<create|update|skip のいずれか>",
            "target_path": "<配置先パス（skipの場合は空文字）>",
            "reason": "<修正理由（150文字以内）>"
        }}
    ]
}}

【action_typeの意味】
- create: 新規ファイルとして作成（既存ファイルを更新しない）
- update: 既存ファイルに統合・更新
- skip: この資料は処理しない（スキップ）

【ルール】
- 変更が必要なものだけ回答に含める
- ユーザーの指示を最優先する
- 「新規作成にして」→ action_type="create"
- 「更新にして」→ action_type="update"
- 「スキップして」→ action_type="skip"
"""
        
        try:
            response = client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {"role": "system", "content": "あなたはナレッジ管理の専門家です。ユーザーの指示に従って提案を修正してください。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_completion_tokens=1000,
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # JSONを抽出
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                result = json.loads(json_match.group())
                
                # 提案を更新
                actions_map = {a.get("source"): a for a in result.get("actions", [])}
                
                for action in self._proposal.actions:
                    if action.source_document in actions_map:
                        new_info = actions_map[action.source_document]
                        
                        # action_typeの変更
                        new_action_type = new_info.get("action_type", "").lower()
                        if new_action_type == "create":
                            action.action_type = ActionType.CREATE
                        elif new_action_type == "update":
                            action.action_type = ActionType.UPDATE
                        elif new_action_type == "partial":
                            action.action_type = ActionType.PARTIAL_UPDATE
                        elif new_action_type == "skip":
                            action.action_type = ActionType.SKIP
                        
                        # 配置先とreasonの変更
                        if new_info.get("target_path"):
                            action.target_path = new_info.get("target_path")
                        action.reason = new_info.get("reason", action.reason)
            
        except Exception as e:
            self.logger.warning(f"AI regeneration failed: {e}")
        
        return self._proposal
    
    def get_existing_info(self) -> Dict:
        """既存ナレッジの情報を取得（表示用）"""
        return {
            "total_categories": self.existing_structure.get("total_categories", 0),
            "total_files": self.existing_structure.get("total_files", 0),
            "categories": list(self.existing_structure.get("categories", {}).keys()),
        }
    
    def format_terminal_output(self, proposal: Proposal) -> str:
        """ターミナル出力用のフォーマット"""
        lines = []
        
        # 新規作成モードと更新モードで出力形式を分岐
        if proposal.mode == UpdateMode.NEW:
            return self._format_new_mode_output(proposal)
        else:
            return self._format_update_mode_output(proposal)
    
    def _format_new_mode_output(self, proposal: Proposal) -> str:
        """新規作成モードの出力フォーマット"""
        lines = []
        
        # カテゴリ別にアクションをグループ化
        categories: Dict[str, List[ProposedAction]] = {}
        for action in proposal.actions:
            if action.action_type == ActionType.CREATE:
                target = action.target_path or ""
                category = target.split("/")[0] if "/" in target else "01_新規"
                if category not in categories:
                    categories[category] = []
                categories[category].append(action)
        
        # ファイル数・カテゴリ数のカウント
        total_files = sum(
            len(a.split_into) if a.split_into else 1
            for actions in categories.values()
            for a in actions
        )
        total_categories = len(categories)
        input_file_count = len(proposal.actions)
        
        # ヘッダー
        lines.append("")
        lines.append("═" * 80)
        lines.append("                        📂 ナレッジベース構成提案")
        lines.append("═" * 80)
        lines.append("")
        lines.append(f"  pre-knowledge/ 内の {input_file_count} ファイルを分析しました。")
        lines.append(f"  {total_categories} カテゴリ、{total_files} ナレッジに整理します。")
        lines.append("")
        lines.append("─" * 80)
        
        # カテゴリ別表示
        for cat_name, actions in sorted(categories.items()):
            lines.append(f"  📁 {cat_name}/")
            lines.append("")
            
            for action in actions:
                source = action.source_document
                char_count = 0
                chapter_count = 0
                
                # ドキュメント情報を取得（あれば）
                for doc in proposal.new_documents:
                    if doc.get("name") == source:
                        char_count = doc.get("char_count", 0)
                        chapter_count = len(doc.get("headings", []))
                        break
                
                if action.split_into:
                    # 分割される場合
                    for split_path in action.split_into:
                        filename = split_path.split("/")[-1] if "/" in split_path else split_path
                        lines.append(f"     ├─ {filename} ← 📄 {source}")
                else:
                    # 単一ファイルの場合
                    target = action.target_path or ""
                    filename = target.split("/")[-1] if "/" in target else target
                    lines.append(f"     ├─ {filename} ← 📄 {source}")
            
            lines.append("")
        
        lines.append("─" * 80)
        lines.append("")
        lines.append("  【ファイル配置】")
        lines.append("       インプットファイル                              ナレッジ")
        lines.append("  " + "─" * 76)
        lines.append("")
        
        # ファイル配置マッピング
        for action in proposal.actions:
            if action.action_type == ActionType.CREATE:
                source = action.source_document
                
                # ドキュメント情報を取得
                char_count = 0
                chapter_count = 0
                for doc in proposal.new_documents:
                    if doc.get("name") == source:
                        char_count = doc.get("char_count", 0)
                        chapter_count = len(doc.get("headings", []))
                        break
                
                # ファイル名の長さを調整
                source_display = source[:30] if len(source) > 30 else source
                info_str = f"({char_count:,}字, {chapter_count}章)" if char_count > 0 else ""
                source_full = f"  📄 {source_display} {info_str}"
                
                if action.split_into and len(action.split_into) > 1:
                    # 複数ファイルに分割
                    first_file = action.split_into[0].split("/")[-1] if "/" in action.split_into[0] else action.split_into[0]
                    category = action.split_into[0].split("/")[0] if "/" in action.split_into[0] else ""
                    
                    lines.append(source_full.ljust(50) + f"─┬→ 📁 {category}/{first_file}")
                    
                    for split_path in action.split_into[1:]:
                        filename = split_path.split("/")[-1] if "/" in split_path else split_path
                        lines.append(" " * 50 + f" └→ 📁 {category}/{filename}")
                else:
                    # 単一ファイル
                    target = action.target_path or ""
                    lines.append(source_full.ljust(50) + f"──→ 📁 {target}")
                
                lines.append("")
        
        # フッター
        lines.append("═" * 80)
        lines.append("")
        lines.append("  📊 サマリー")
        lines.append(f"  └─ ✨ 新規作成: {len(proposal.actions)}件 → {total_files}ファイル")
        lines.append("")
        lines.append("  ❓ 実行する場合は [Y]、修正依頼がある場合は指示を入力してください")
        lines.append("")
        
        return "\n".join(lines)
    
    def _format_update_mode_output(self, proposal: Proposal) -> str:
        """更新モードの出力フォーマット"""
        lines = []
        
        # ヘッダー
        lines.append("")
        lines.append("═" * 80)
        lines.append("                        📊 ナレッジ化提案")
        lines.append("═" * 80)
        lines.append("")
        lines.append(f"  📁 出力先: {self.output_dir}")
        
        if proposal.mode == UpdateMode.INCREMENTAL:
            info = self.get_existing_info()
            lines.append(f"  📝 モード: 更新（既存: {info['total_categories']}カテゴリ, {info['total_files']}ファイル）")
            lines.append("  📏 判定基準:")
            lines.append("      ├─ 類似度 80%以上 → 更新（既存ファイルに統合）")
            lines.append("      ├─ 類似度 80%未満 → 新規追加")
            lines.append("      └─ 処理済み/除外設定/変更なし → スキップ")
        else:
            lines.append("  📝 モード: 抜本的変更が推奨されます")
            lines.append(f"  ⚠️ 理由: {proposal.restructure_reason}")
        
        lines.append("")
        
        if True:  # 常に表示（更新モードなので）
            # 分析結果（カード形式）
            action_count = len(proposal.actions)
            lines.append("─" * 80)
            lines.append(f"                        🔍 分析結果（{action_count}件）")
            lines.append("─" * 80)
            lines.append("")
            
            for idx, action in enumerate(proposal.actions, 1):
                icon = {
                    ActionType.CREATE: "✨ 新規追加",
                    ActionType.UPDATE: "📝 更新",
                    ActionType.PARTIAL_UPDATE: "🔀 部分更新",
                    ActionType.SKIP: "⏭️ スキップ",
                }[action.action_type]
                
                # 類似度を表示（0より大きい場合のみ）
                sim_str = f"（類似度: {action.similarity*100:.0f}%）" if action.similarity > 0 else ""
                lines.append(f"  [{idx}] {icon}{sim_str}")
                lines.append(f"      📄 入力: {action.source_document}")
                
                if action.action_type == ActionType.CREATE:
                    target = action.target_path or '-'
                    category = target.split("/")[0] if "/" in target else "新規"
                    
                    if action.split_into:
                        # 分割する場合はカテゴリと分割ファイル一覧のみ表示
                        lines.append(f"      📁 配置先カテゴリ: {category}")
                        lines.append(f"      📦 分割作成: {len(action.split_into)}ファイル")
                        for split_path in action.split_into[:7]:
                            split_filename = split_path.split("/")[-1] if "/" in split_path else split_path
                            lines.append(f"          └─ {split_filename}")
                        if len(action.split_into) > 7:
                            lines.append(f"          └─ ... 他{len(action.split_into) - 7}件")
                    else:
                        # 分割しない場合はファイル名も表示
                        filename = target.split("/")[-1] if "/" in target else target
                        lines.append(f"      📁 配置先カテゴリ: {category}")
                        lines.append(f"      📄 ファイル名: {filename}")
                elif action.action_type == ActionType.UPDATE:
                    # 複数ファイルへの更新がある場合
                    if action.multi_target and action.multi_target.targets:
                        total_sections = action.multi_target.total_sections
                        num_targets = len(action.multi_target.targets)
                        
                        if num_targets > 1:
                            lines.append(f"      📊 セクション分析: {total_sections}セクションを{num_targets}ファイルに分配")
                        else:
                            lines.append(f"      📊 処理内容: {total_sections}セクションを既存ファイルに統合")
                        lines.append("")
                        
                        # 各ターゲットファイルの情報を表示
                        for i, target in enumerate(action.multi_target.targets, 1):
                            file_name = Path(target.file_path).name
                            update_count = len(target.update_sections)
                            new_count = len(target.new_sections)
                            
                            if target.action == "create":
                                lines.append(f"      📁 新規作成{i}: {target.file_path}")
                            else:
                                if num_targets > 1:
                                    lines.append(f"      📁 更新先{i}: {target.file_path}")
                                else:
                                    lines.append(f"      📁 更新先: {target.file_path}")
                            
                            # 更新セクション
                            if update_count > 0:
                                lines.append(f"          📝 内容更新: {update_count}セクション")
                                for section in target.update_sections[:2]:
                                    line_info = target.update_lines.get(section, "")
                                    section_short = section[:20]
                                    if line_info:
                                        lines.append(f"              └─ {section_short} ({line_info})")
                                    else:
                                        lines.append(f"              └─ {section_short}")
                                if update_count > 2:
                                    lines.append(f"              └─ ... 他{update_count - 2}件")
                            
                            # 新規セクション
                            if new_count > 0:
                                lines.append(f"          ✨ 新規追加: {new_count}セクション")
                                for section in target.new_sections[:2]:
                                    section_short = section[:20]
                                    lines.append(f"              └─ {section_short}")
                                if new_count > 2:
                                    lines.append(f"              └─ ... 他{new_count - 2}件")
                        
                        # 変更なしセクション（全体で表示）
                        unchanged_count = len(action.multi_target.unchanged_sections)
                        if unchanged_count > 0:
                            lines.append(f"      ✅ 変更なし: {unchanged_count}セクション（内容同一のためスキップ）")
                    
                    # 従来の表示（multi_targetがない場合のフォールバック）
                    elif action.changes:
                        lines.append(f"      📁 更新先: {action.target_path or '-'}")
                        # セクションを分類
                        new_sections = [c for c in action.changes if "新規" in c.item or "✨" in c.item]
                        update_sections = [c for c in action.changes if "更新" in c.item and "📝" in c.item]
                        retain_sections = [c for c in action.changes if "保持" in c.item or "📌" in c.item]
                        
                        # 処理の概要を表示
                        total_changes = len(new_sections) + len(update_sections)
                        lines.append(f"      📊 処理内容: 入力資料の全{total_changes}セクションを既存ファイルに統合")
                        
                        # 更新セクション（内容が変更されるもの）
                        if update_sections:
                            lines.append(f"      📝 内容更新: {len(update_sections)}セクション")
                            for change in update_sections[:3]:
                                section_name = change.item.replace("📝 ", "").replace("更新: ", "")[:25]
                                line_info = change.target_line if change.target_line else ""
                                if line_info:
                                    lines.append(f"          └─ {section_name} ({line_info})")
                                else:
                                    lines.append(f"          └─ {section_name}")
                            if len(update_sections) > 3:
                                lines.append(f"          └─ ... 他{len(update_sections) - 3}件")
                        
                        # 新規セクション（既存にないもの）
                        if new_sections:
                            lines.append(f"      ✨ 新規追加: {len(new_sections)}セクション（末尾に追加）")
                            for change in new_sections[:3]:
                                section_name = change.item.replace("✨ ", "").replace("新規: ", "")[:25]
                                lines.append(f"          └─ {section_name}")
                            if len(new_sections) > 3:
                                lines.append(f"          └─ ... 他{len(new_sections) - 3}件")
                        
                        # 保持セクション（既存のみにあるもの）
                        if retain_sections:
                            for change in retain_sections:
                                lines.append(f"      {change.item}")
                    else:
                        # 変更詳細がない場合
                        lines.append(f"      📁 更新先: {action.target_path or '-'}")
                        lines.append(f"      📊 処理内容: 入力資料を既存ファイルに統合")
                elif action.action_type == ActionType.PARTIAL_UPDATE:
                    # 部分更新の表示
                    partial = action.partial_info
                    if partial:
                        lines.append(f"      📁 更新先: {partial.update_target}")
                        lines.append(f"          └─ 更新: {partial.update_char_count:,}文字 ({len(partial.update_sections)}セクション)")
                        lines.append(f"      📁 新規先: {partial.create_target}")
                        lines.append(f"          └─ 新規: {partial.create_char_count:,}文字 ({len(partial.create_sections)}セクション)")
                        
                        # セクション名の例を表示
                        if partial.update_sections:
                            sections_str = "、".join(partial.update_sections[:3])
                            if len(partial.update_sections) > 3:
                                sections_str += f" 他{len(partial.update_sections) - 3}件"
                            lines.append(f"      📝 更新セクション: {sections_str}")
                        if partial.create_sections:
                            sections_str = "、".join(partial.create_sections[:3])
                            if len(partial.create_sections) > 3:
                                sections_str += f" 他{len(partial.create_sections) - 3}件"
                            lines.append(f"      ✨ 新規セクション: {sections_str}")
                    else:
                        lines.append(f"      📁 対象: {action.target_path or '-'}")
                else:
                    lines.append(f"      📁 対象: -")
                
                # 判断理由を150文字以内で表示
                reason = action.reason[:150] if len(action.reason) > 150 else action.reason
                lines.append(f"      💭 判断理由: {reason}")
                
                lines.append("")
        
        # フッター
        lines.append("═" * 80)
        lines.append("")
        
        # サマリー
        lines.append("  📊 サマリー")
        lines.append(f"  ├─ ✨ 新規追加: {proposal.summary.get('create', 0)}件")
        lines.append(f"  ├─ 📝 更新: {proposal.summary.get('update', 0)}件")
        lines.append(f"  └─ ⏭️ スキップ: {proposal.summary.get('skip', 0)}件")
        lines.append("")
        
        # 更新がある場合のみレポート出力先を表示
        update_count = proposal.summary.get('update', 0)
        if update_count > 0:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            lines.append(f"  📝 更新 詳細レポート: {self.output_dir}/UPDATE_REPORT_{timestamp}.md")
            lines.append("")
        
        # 実行確認のヒント
        lines.append("  ❓ 実行する場合は [Y]、修正依頼がある場合は指示を入力してください")
        lines.append("")
        
        return "\n".join(lines)
