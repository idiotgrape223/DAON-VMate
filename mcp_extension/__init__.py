"""
DAON-VMate용 MCP 서버 확장 영역.

- 기본 통합 설정: ``mcp_extension/mcp_servers.json`` (settings.yaml ``mcp.config_file``)
- 개발자/개인 서버: ``mcp_extension/servers/<이름>/`` 아래에 코드를 두고,
  같은 폴더에 ``mcp_servers.fragment.json``을 두면 병합됩니다
  (``loader.load_merged_mcp_servers``).
"""

DEFAULT_MCP_SERVERS_CONFIG_FILE = "mcp_extension/mcp_servers.json"
SERVERS_SUBDIR = "servers"
FRAGMENT_FILENAME = "mcp_servers.fragment.json"
