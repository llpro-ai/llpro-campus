"""LangChain tools for LLPRO's Ground Truth, Countersign and Agent Record."""
import json
from typing import Any, ClassVar, Dict, List, Optional, Type

from langchain_core.tools import BaseTool, ToolException
from pydantic import BaseModel, Field

from . import client


class _LLPROTool(BaseTool):
    """Shared plumbing: one POST to one LLPRO endpoint; errors reach the agent as text (handle_tool_error)."""

    api_key: Optional[str] = Field(default=None, repr=False, exclude=True)
    agent_id: Optional[str] = None
    timeout: float = 60.0
    handle_tool_error: bool = True
    endpoint: ClassVar[str] = ""

    def _post(self, payload: Dict[str, Any]) -> str:
        try:
            out = client.call(self.endpoint, payload, api_key=self.api_key, agent_id=self.agent_id, timeout=self.timeout)
        except client.LLPROError as e:
            raise ToolException(str(e))
        return json.dumps(out, ensure_ascii=False)


class GroundTruthInput(BaseModel):
    topic: str = Field(description="One of: weather, weather_alerts, river_level, flood, earthquakes, disasters, "
                                   "air_quality, road_closures, power_outages, burn_ban")
    place: Optional[str] = Field(default=None, description="Place name, e.g. 'Lafayette, LA' or 'Lagos, Nigeria'")
    lat: Optional[float] = Field(default=None, description="Latitude (instead of place)")
    lon: Optional[float] = Field(default=None, description="Longitude (instead of place)")
    max_age_minutes: Optional[int] = Field(default=None, description="Oldest answer you accept, in minutes")


class LLPROGroundTruth(_LLPROTool):
    name: str = "llpro_ground_truth"
    description: str = ("One cited, time-stamped public fact for a place: weather, weather alerts, river level, flood, "
                        "earthquakes, disasters, air quality, road closures, power outages or burn bans. "
                        "Returns JSON with the answer, its source and when it was observed.")
    args_schema: Type[BaseModel] = GroundTruthInput
    endpoint: ClassVar[str] = "ground_truth"

    def _run(self, topic: str, place: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None,
             max_age_minutes: Optional[int] = None, run_manager: Any = None) -> str:
        p = {"topic": topic, "place": place, "lat": lat, "lon": lon, "max_age_minutes": max_age_minutes}
        return self._post({k: v for k, v in p.items() if v is not None})


class CountersignInput(BaseModel):
    output: str = Field(description="The work to check: short claims such as '12 * 4 = 48', a date, a unit "
                                    "conversion or a citation. Stamps are logged publicly: never send private text.")
    kind: Optional[str] = Field(default=None, description="calculation, citation, date or general")


class LLPROCountersign(_LLPROTool):
    name: str = "llpro_countersign"
    description: str = ("An independent signed check of an agent's work (sums, dates, units, citations). Returns a "
                        "verdict and a stamp that is logged publicly and can be verified by anyone. "
                        "Do not send private or personal text.")
    args_schema: Type[BaseModel] = CountersignInput
    endpoint: ClassVar[str] = "countersign"
    producer_agent_id: str = "langchain-agent"
    producer_model: Optional[str] = None

    def _run(self, output: str, kind: Optional[str] = None, run_manager: Any = None) -> str:
        producer = {"agent_id": self.producer_agent_id}
        if self.producer_model:
            producer["model"] = self.producer_model
        p = {"output": output, "producer": producer}
        if kind:
            p["kind"] = kind
        return self._post(p)


class AgentRecordInput(BaseModel):
    target: str = Field(description="URL, registry name or agent id of an agent or MCP server, under 600 characters")


class LLPROAgentRecord(_LLPROTool):
    name: str = "llpro_agent_record"
    description: str = ("The public record of an agent or MCP server before you rely on it: directory listings, "
                        "uptime checks, receipts and disputes, by URL, registry name or agent id.")
    args_schema: Type[BaseModel] = AgentRecordInput
    endpoint: ClassVar[str] = "agent_record"

    def _run(self, target: str, run_manager: Any = None) -> str:
        return self._post({"target": target})


def get_tools(api_key: Optional[str] = None, agent_id: Optional[str] = None, timeout: float = 60.0) -> List[BaseTool]:
    """All three tools. api_key: prepaid credit key (else LLPRO_API_KEY). agent_id: sent as X-Agent-Id."""
    kw = {"api_key": api_key, "agent_id": agent_id, "timeout": timeout}
    return [LLPROGroundTruth(**kw), LLPROCountersign(**kw), LLPROAgentRecord(**kw)]
