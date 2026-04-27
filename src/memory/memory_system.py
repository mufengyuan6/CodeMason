"""三层记忆系统."""

import json
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


@dataclass
class MemoryEntry:
    """记忆条目."""
    key: str
    value: Any
    level: int  # 1=short, 2=medium, 3=long
    timestamp: float
    access_count: int = 0
    last_accessed: float = 0.0


class ShortTermMemory:
    """L1: 短期记忆."""
    
    def __init__(self, ttl: int = 86400):  # 24小时
        self.ttl = ttl
        self.storage: Dict[str, MemoryEntry] = {}
    
    def store(self, key: str, value: Any):
        """存储记忆."""
        self.storage[key] = MemoryEntry(
            key=key,
            value=value,
            level=1,
            timestamp=time.time(),
            last_accessed=time.time()
        )
    
    def retrieve(self, key: str) -> Optional[Any]:
        """检索记忆."""
        entry = self.storage.get(key)
        if not entry:
            return None
        
        # 检查是否过期
        if time.time() - entry.timestamp > self.ttl:
            del self.storage[key]
            return None
        
        # 更新访问信息
        entry.access_count += 1
        entry.last_accessed = time.time()
        
        return entry.value
    
    def get_recent(self, limit: int = 10) -> List[Dict]:
        """获取最近的记忆."""
        entries = sorted(
            self.storage.values(),
            key=lambda x: x.last_accessed,
            reverse=True
        )
        return [asdict(e) for e in entries[:limit]]
    
    def clear_expired(self):
        """清理过期记忆."""
        now = time.time()
        expired = [
            k for k, v in self.storage.items()
            if now - v.timestamp > self.ttl
        ]
        for k in expired:
            del self.storage[k]


class MediumTermMemory:
    """L2: 中期记忆 - 项目级Bug模式."""
    
    def __init__(self, file_path: str = "memory/medium_term.json"):
        self.file_path = file_path
        self.patterns: Dict[str, Any] = {}
        self._load()
    
    def store_bug_pattern(self, pattern: Dict):
        """存储Bug模式."""
        key = f"bug_{pattern.get('type', 'unknown')}_{int(time.time())}"
        self.patterns[key] = {
            'pattern': pattern,
            'timestamp': time.time(),
            'count': 1
        }
        self._save()
    
    def find_similar_bugs(self, error_type: str) -> List[Dict]:
        """查找相似Bug."""
        similar = []
        for key, value in self.patterns.items():
            if key.startswith(f"bug_{error_type}"):
                similar.append(value)
        return similar
    
    def _load(self):
        """加载记忆."""
        try:
            with open(self.file_path, 'r') as f:
                self.patterns = json.load(f)
        except FileNotFoundError:
            self.patterns = {}
    
    def _save(self):
        """保存记忆."""
        import os
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        with open(self.file_path, 'w') as f:
            json.dump(self.patterns, f, indent=2)


class LongTermMemory:
    """L3: 长期记忆 - 跨项目通用模式."""
    
    def __init__(self):
        self.patterns: Dict[str, Any] = {}
    
    def store_general_pattern(self, category: str, pattern: Dict):
        """存储通用模式."""
        if category not in self.patterns:
            self.patterns[category] = []
        
        self.patterns[category].append({
            'pattern': pattern,
            'timestamp': time.time(),
            'frequency': 1
        })
    
    def retrieve_patterns(self, category: str) -> List[Dict]:
        """检索模式."""
        return self.patterns.get(category, [])
    
    def increment_frequency(self, category: str, pattern_id: str):
        """增加频率计数."""
        if category in self.patterns:
            for p in self.patterns[category]:
                if p.get('pattern', {}).get('id') == pattern_id:
                    p['frequency'] = p.get('frequency', 0) + 1


class MemoryManager:
    """记忆管理器."""
    
    def __init__(self):
        self.l1_short = ShortTermMemory()
        self.l2_medium = MediumTermMemory()
        self.l3_long = LongTermMemory()
    
    def remember(self, key: str, value: Any, level: int = 1):
        """
        记忆存储.
        
        Args:
            key: 记忆键
            value: 记忆值
            level: 记忆级别 (1=short, 2=medium, 3=long)
        """
        if level == 1:
            self.l1_short.store(key, value)
        elif level == 2:
            self.l2_medium.store_bug_pattern(value)
        elif level == 3:
            if isinstance(value, dict) and 'category' in value:
                self.l3_long.store_general_pattern(value['category'], value)
    
    def recall(self, key: str, level: int = 1) -> Optional[Any]:
        """记忆检索."""
        if level == 1:
            return self.l1_short.retrieve(key)
        return None
    
    def get_session_context(self, session_id: str) -> Dict:
        """获取会话上下文."""
        return {
            'session_id': session_id,
            'recent_memories': self.l1_short.get_recent(5),
            'bug_patterns': self.l2_medium.find_similar_bugs('syntax'),
            'general_patterns': self.l3_long.retrieve_patterns('optimization')
        }
    
    def consolidate(self):
        """记忆整合 - 从L1到L2/L3的迁移."""
        # 高频访问的L1记忆提升到L2
        recent = self.l1_short.get_recent(20)
        for mem in recent:
            if mem.get('access_count', 0) > 5:
                # 提升到L2
                self.l2_medium.store_bug_pattern({
                    'type': 'frequent_pattern',
                    'data': mem
                })
        
        # 清理过期记忆
        self.l1_short.clear_expired()
    
    def get_stats(self) -> Dict:
        """获取记忆统计."""
        return {
            'l1_short_count': len(self.l1_short.storage),
            'l2_medium_count': len(self.l2_medium.patterns),
            'l3_long_categories': len(self.l3_long.patterns),
            'total_patterns': sum(
                len(p) for p in self.l3_long.patterns.values()
            )
        }