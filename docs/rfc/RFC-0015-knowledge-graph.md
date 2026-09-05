# RFC-0015: Knowledge Graph

| Field | Value |
|---|---|
| **Status** | Draft |
| **Depends on** | RFC-0014, RFC-0009 (Knowledge Objects) |
| **Blocks** | — |

## 1. Цель

Построить **граф знаний** поверх Data Lake, чтобы можно было отвечать на вопросы вида:

- «Какие live edges используют Feature `rsi_14`?»
- «Граф зависимостей edge `sweep_btc_5m_trend_funding_neg`»
- «Все trades, опирающиеся на edge, который сейчас `decaying`»
- «Conflicting edges для BTCUSDT»

## 2. Архитектура

```
                    ┌──────────────────────┐
                    │   Knowledge Graph    │
                    │                      │
                    │  Nodes + Edges       │
                    │  + индексы           │
                    └──────────┬───────────┘
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
        ┌──────────────┐              ┌──────────────┐
        │  Storage     │              │  Query API   │
        │  Backend     │              │  (cypher-like)│
        └──────┬───────┘              └──────┬───────┘
               │                             │
               └──────────┬──────────────────┘
                          ▼
                  ┌──────────────┐
                  │  Data Lake   │
                  │  (events)    │
                  └──────────────┘
```

## 3. Nodes (типы)

| Type | Описание | Primary source |
|---|---|---|
| `Knowledge` | Knowledge Object (RFC-0009) | knowledge YAML |
| `Feature` | Computed feature | Data Lake `FeatureComputed` |
| `Regime` | Detected regime | Data Lake `RegimeDetected` |
| `Signal` | Generated signal | Data Lake `SignalGenerated` |
| `Decision` | Made decision | Data Lake `DecisionCreated` |
| `Order` | Submitted order | Data Lake `OrderSubmitted` |
| `Trade` | Closed trade | Data Lake `TradeRecorded` |
| `Evidence` | Statistical evidence | Data Lake `EvidenceGenerated` |
| `Edge` | Discovered edge | Data Lake `EdgeDiscovered` |
| `Replay` | Replay session | Data Lake `ReplayStarted` |
| `Shadow` | Shadow result | Data Lake `ShadowResult` |
| `Anomaly` | Anomaly | Data Lake `AnomalyDetected` |

## 4. Edges (типы отношений)

| Relation | From | To | Cardinality |
|---|---|---|---|
| `uses` | Edge | Feature | N:N |
| `requires` | Edge | Regime | N:N |
| `derived_from` | Signal | Feature | N:N |
| `generated_by` | Signal | Regime | N:1 |
| `based_on` | Decision | Signal | N:1 |
| `created` | Order | Decision | 1:1 |
| `resulted_in` | Order | Trade | 1:1 |
| `evidence_for` | Evidence | Edge | 1:N |
| `supersedes` | Edge | Edge | N:1 |
| `conflicts_with` | Edge | Edge | N:N |
| `applies_to` | Edge | Market | N:N |
| `discovered_in` | Edge | Replay | N:N |
| `validated_by` | Edge | Shadow | N:N |
| `invalidated_by` | Trade | Edge | N:1 (decay evidence) |

## 5. Storage Backend Interface

```python
class GraphBackend(ABC):
    @abstractmethod
    async def add_node(self, node: GraphNode) -> None: ...
    
    @abstractmethod
    async def add_edge(self, edge: GraphEdge) -> None: ...
    
    @abstractmethod
    async def get_node(self, node_id: UUID) -> GraphNode | None: ...
    
    @abstractmethod
    async def get_neighbors(
        self, 
        node_id: UUID, 
        relation: str | None = None,
        direction: str = "out"  # out, in, both
    ) -> list[GraphNode]: ...
    
    @abstractmethod
    async def get_path(
        self, 
        from_id: UUID, 
        to_id: UUID,
        max_depth: int = 5
    ) -> list[GraphEdge]: ...
    
    @abstractmethod
    async def query_cypher(
        self, 
        query: str, 
        params: dict = {}
    ) -> list[dict]: ...
```

## 6. Реализации

| Backend | Когда | Описание |
|---|---|---|
| `InMemoryGraph` | MVP | NetworkX, fast, для <100K nodes |
| `SQLiteGraph` | Phase B | SQLite с рекурсивными CTE |
| `Neo4jBackend` | Phase C | Production graph DB |
| `MemgraphBackend` | Phase C | Alternative |

MVP — `InMemoryGraph` через NetworkX, с persist в Data Lake.

## 7. Query API

### 7.1 Pythonic API

```python
# Все edges, использующие Feature
edges = await graph.get_neighbors(
    feature_id, 
    relation="used_by",
    direction="in"
)

# Граф зависимостей edge
deps = await graph.get_neighbors(
    edge_id, 
    direction="out"
)
# → [Feature, Regime, Evidence, Replay, ...]

# Path: edge → trade
path = await graph.get_path(edge_id, trade_id)
# → [Edge -uses-> Feature, Edge -validated_by-> Evidence, Trade -based_on-> Edge]
```

### 7.2 Cypher-like queries

```python
# Найти все live edges для BTCUSDT
result = await graph.query("""
    MATCH (e:Edge)-[:applies_to]->(m:Market {symbol: 'BTCUSDT'})
    WHERE e.status = 'live'
    RETURN e
""")

# Decaying edges с evidence
result = await graph.query("""
    MATCH (e:Edge {status: 'decaying'})-[:evidence_for]-(ev:Evidence)
    RETURN e, ev
""")
```

## 8. Build process (auto-sync с Data Lake)

```python
class GraphBuilder:
    """Слушает Data Lake и обновляет граф."""
    
    def __init__(self, graph: GraphBackend, data_lake: DataLakeService):
        self.graph = graph
        self.data_lake = data_lake
    
    async def run(self):
        # Cold rebuild from scratch
        events = await self.data_lake.query(limit=10_000_000)
        await self._build_from_events(events)
        
        # Live updates
        async for event in self.data_lake.subscribe():
            await self._process_event(event)
    
    async def _process_event(self, event: dict):
        if event["event_type"] == "FeatureComputed":
            await self.graph.add_node(Feature.from_event(event))
        elif event["event_type"] == "SignalGenerated":
            ...
        # etc.
```

## 9. UI: Graph Explorer

Web UI, который показывает:
- Subgraph по выбранному edge
- Filter by node type, time range, status
- Highlight `decaying` edges красным
- Click на node → timeline всех связанных events

## 10. Performance

- MVP InMemory: <100K nodes, запросы < 100ms
- Phase B SQLite: до 1M nodes
- Phase C Neo4j: до 100M nodes

## 11. Что входит в MVP

- `InMemoryGraph` через NetworkX
- Build из Data Lake (cold rebuild + live sync)
- Pythonic API
- Простой Web Viewer (subgraph render)

## 12. Что дальше

- Реализация `core/knowledge_graph/`
- Auto-generated documentation graph
- Integration с Evolution Engine (RFC-0015 в новой серии)
