"""代码解析器测试."""

import pytest
from src.parser import PythonParser, JavaScriptParser, GoParser
from src.parser.base import SymbolType


class TestPythonParser:
    """Python解析器测试."""
    
    def test_parse_function(self):
        """测试函数解析."""
        code = '''
def hello_world(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"
'''
        parser = PythonParser()
        result = parser.parse(code, "test.py")
        
        assert len(result.symbols) == 1
        func = result.symbols[0]
        assert func.name == "hello_world"
        assert func.symbol_type == SymbolType.FUNCTION
        assert func.docstring is not None
        assert "hello" in func.docstring.lower()
    
    def test_parse_class(self):
        """测试类解析."""
        code = '''
class UserService:
    """Service for user management."""
    
    def get_user(self, user_id: int):
        """Get user by ID."""
        return {"id": user_id}
'''
        parser = PythonParser()
        result = parser.parse(code, "test.py")
        
        classes = [s for s in result.symbols if s.symbol_type == SymbolType.CLASS]
        methods = [s for s in result.symbols if s.symbol_type == SymbolType.METHOD]
        
        assert len(classes) == 1
        assert classes[0].name == "UserService"
        assert len(methods) == 1
        assert methods[0].name == "get_user"
        assert methods[0].parent_name == "UserService"
    
    def test_parse_imports(self):
        """测试导入解析."""
        code = '''
import os
import sys
from typing import List, Dict
'''
        parser = PythonParser()
        result = parser.parse(code, "test.py")
        
        assert len(result.imports) >= 2
        import_names = [imp.name for imp in result.imports]
        assert "os" in import_names or "typing" in import_names
    
    def test_parse_dependencies(self):
        """测试依赖提取."""
        code = '''
def process_data():
    result = helper_function()
    return result

def helper_function():
    return 42
'''
        parser = PythonParser()
        result = parser.parse(code, "test.py")
        
        # 检查依赖关系
        process_func = next(s for s in result.symbols if s.name == "process_data")
        assert "helper_function" in process_func.dependencies


class TestJavaScriptParser:
    """JavaScript解析器测试."""
    
    def test_parse_function(self):
        """测试函数解析."""
        code = '''
function calculateSum(a, b) {
    return a + b;
}

const multiply = (a, b) => a * b;
'''
        parser = JavaScriptParser()
        result = parser.parse(code, "test.js")
        
        funcs = [s for s in result.symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) >= 2
        
        names = [f.name for f in funcs]
        assert "calculateSum" in names
        assert "multiply" in names
    
    def test_parse_class(self):
        """测试类解析."""
        code = '''
class Animal {
    constructor(name) {
        this.name = name;
    }
    
    speak() {
        console.log(`${this.name} makes a sound.`);
    }
}
'''
        parser = JavaScriptParser()
        result = parser.parse(code, "test.js")
        
        classes = [s for s in result.symbols if s.symbol_type == SymbolType.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "Animal"


class TestGoParser:
    """Go解析器测试."""
    
    def test_parse_function(self):
        """测试函数解析."""
        code = '''
package main

import "fmt"

func Hello(name string) string {
    return fmt.Sprintf("Hello, %s!", name)
}
'''
        parser = GoParser()
        result = parser.parse(code, "test.go")
        
        funcs = [s for s in result.symbols if s.symbol_type == SymbolType.FUNCTION]
        assert len(funcs) >= 1
        assert any(f.name == "Hello" for f in funcs)
    
    def test_parse_struct(self):
        """测试结构体解析."""
        code = '''
type User struct {
    ID   int
    Name string
}

func (u *User) GetName() string {
    return u.Name
}
'''
        parser = GoParser()
        result = parser.parse(code, "test.go")
        
        types = [s for s in result.symbols if s.symbol_type == SymbolType.CLASS]
        methods = [s for s in result.symbols if s.symbol_type == SymbolType.METHOD]
        
        assert len(types) >= 1
        assert any(t.name == "User" for t in types)
        assert len(methods) >= 1


class TestParserFactory:
    """解析器工厂测试."""
    
    def test_get_parser_for_python(self):
        """测试Python文件解析器选择."""
        from src.parser import get_parser_for_file
        parser = get_parser_for_file("test.py")
        assert isinstance(parser, PythonParser)
    
    def test_get_parser_for_js(self):
        """测试JS文件解析器选择."""
        from src.parser import get_parser_for_file
        parser = get_parser_for_file("test.js")
        assert isinstance(parser, JavaScriptParser)
    
    def test_get_parser_for_go(self):
        """测试Go文件解析器选择."""
        from src.parser import get_parser_for_file
        parser = get_parser_for_file("test.go")
        assert isinstance(parser, GoParser)
    
    def test_unsupported_file(self):
        """测试不支持的文件类型."""
        from src.parser import get_parser_for_file
        with pytest.raises(ValueError):
            get_parser_for_file("test.java")