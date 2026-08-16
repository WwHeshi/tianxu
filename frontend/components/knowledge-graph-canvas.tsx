"use client";

import {
  Database,
  LocateFixed,
  Maximize2,
  Minus,
  Network,
  Plus,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import dynamic from "next/dynamic";
import {
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  ForceGraphMethods,
  GraphData,
  LinkObject,
  NodeObject,
} from "react-force-graph-2d";

import type {
  KnowledgeGraphNode,
  KnowledgeGraphRelationship,
  KnowledgeGraphSnapshot,
  KnowledgeGraphStatus,
} from "@/lib/types";

type ForceGraphComponent = typeof import("react-force-graph-2d")["default"];

const ForceGraph2D = dynamic(
  () => import("react-force-graph-2d"),
  { ssr: false },
) as ForceGraphComponent;

const KIND_ORDER = ["Source", "Rule", "ConditionGroup", "Condition", "Concept", "Outcome"];

const KIND_META: Record<string, { label: string; color: string; soft: string }> = {
  Source: { label: "来源", color: "#88765c", soft: "#eee8dd" },
  Rule: { label: "规则", color: "#287564", soft: "#dceee8" },
  ConditionGroup: { label: "条件组", color: "#7c6baa", soft: "#ece8f5" },
  Condition: { label: "条件", color: "#b47a25", soft: "#f7ead0" },
  Concept: { label: "概念", color: "#5876a5", soft: "#e4eaf4" },
  Outcome: { label: "结论", color: "#a15e72", soft: "#f2e1e6" },
};

const RELATION_LABELS: Record<string, string> = {
  SOURCED_FROM: "取自",
  RELATES_TO: "关联概念",
  HAS_CONDITION_GROUP: "任一条件组",
  REQUIRES: "必须满足",
  EXCLUDES: "不得出现",
  STRENGTHENS: "增强",
  WEAKENS: "削弱",
  PRODUCES: "产生结论",
  DOES_NOT_PROVE: "不能证明",
  REFINES: "细化",
  EXCEPTION_TO: "例外于",
  CONTRADICTS: "冲突",
};

const KIND_CENTERS: Record<string, { x: number; y: number }> = {
  Source: { x: 0, y: -540 },
  Rule: { x: 0, y: 0 },
  ConditionGroup: { x: -430, y: 60 },
  Condition: { x: -850, y: 80 },
  Concept: { x: 650, y: -180 },
  Outcome: { x: 650, y: 430 },
};

interface VisualNodeData extends KnowledgeGraphNode {
  degree: number;
}

type VisualNode = NodeObject<VisualNodeData>;
type VisualLink = LinkObject<VisualNodeData, KnowledgeGraphRelationship>;

interface NodeConnection {
  relationship: KnowledgeGraphRelationship;
  neighbor: KnowledgeGraphNode;
  outgoing: boolean;
}

interface KnowledgeGraphCanvasProps {
  status: KnowledgeGraphStatus | null;
  snapshot: KnowledgeGraphSnapshot | null;
}

function endpointId(value: unknown): string | null {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (!value || typeof value !== "object" || !("id" in value)) return null;
  const id = value.id;
  return typeof id === "string" || typeof id === "number" ? String(id) : null;
}

function truncateLabel(value: string, maximum = 20): string {
  return value.length > maximum ? `${value.slice(0, maximum - 1)}…` : value;
}

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
  context.fill();
  context.stroke();
}

function createKindClusterForce() {
  let nodes: VisualNode[] = [];
  const force = (alpha: number) => {
    for (const node of nodes) {
      if (typeof node.x !== "number" || typeof node.y !== "number") continue;
      const center = KIND_CENTERS[node.kind] ?? { x: 0, y: 0 };
      const strength = node.kind === "Rule" ? 0.045 : 0.07;
      node.vx = (node.vx ?? 0) + (center.x - node.x) * strength * alpha;
      node.vy = (node.vy ?? 0) + (center.y - node.y) * strength * alpha;
    }
  };
  force.initialize = (values: NodeObject[]) => {
    nodes = values as VisualNode[];
  };
  return force;
}

export function KnowledgeGraphCanvas({ status, snapshot }: KnowledgeGraphCanvasProps) {
  const connected = status?.connected === true;
  const hasData = connected && (snapshot?.nodes.length ?? 0) > 0;
  const snapshotFailed = connected && (status?.node_count ?? 0) > 0 && snapshot === null;
  const stageRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods | undefined>(undefined);
  const hasFittedRef = useRef(false);
  const forcesConfiguredRef = useRef(false);
  const [dimensions, setDimensions] = useState({ width: 900, height: 650 });
  const [visibleKinds, setVisibleKinds] = useState<string[]>(KIND_ORDER);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [focusMode, setFocusMode] = useState(false);

  const nodeById = useMemo(
    () => new Map((snapshot?.nodes ?? []).map((node) => [node.id, node])),
    [snapshot],
  );
  const degreeById = useMemo(() => {
    const degrees = new Map<string, number>();
    for (const relationship of snapshot?.relationships ?? []) {
      degrees.set(relationship.source, (degrees.get(relationship.source) ?? 0) + 1);
      degrees.set(relationship.target, (degrees.get(relationship.target) ?? 0) + 1);
    }
    return degrees;
  }, [snapshot]);
  const graphData = useMemo<GraphData<VisualNodeData, KnowledgeGraphRelationship>>(
    () => {
      const indexes = new Map<string, number>();
      const nodes = (snapshot?.nodes ?? []).map((node) => {
        const index = indexes.get(node.kind) ?? 0;
        indexes.set(node.kind, index + 1);
        const center = KIND_CENTERS[node.kind] ?? { x: 0, y: 0 };
        const angle = index * Math.PI * (3 - Math.sqrt(5));
        const radius = 12 * Math.sqrt(index);
        return {
          ...node,
          degree: degreeById.get(node.id) ?? 0,
          x: center.x + Math.cos(angle) * radius,
          y: center.y + Math.sin(angle) * radius,
        };
      });
      return {
        nodes,
        links: (snapshot?.relationships ?? []).map((relationship) => ({
          ...relationship,
          source: relationship.source,
          target: relationship.target,
        })),
      };
    },
    [degreeById, snapshot],
  );
  const kindCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of snapshot?.nodes ?? []) {
      counts.set(node.kind, (counts.get(node.kind) ?? 0) + 1);
    }
    return counts;
  }, [snapshot]);
  const availableKinds = useMemo(
    () => [...kindCounts.keys()].sort((left, right) => {
      const leftIndex = KIND_ORDER.indexOf(left);
      const rightIndex = KIND_ORDER.indexOf(right);
      return (leftIndex < 0 ? 99 : leftIndex) - (rightIndex < 0 ? 99 : rightIndex)
        || left.localeCompare(right);
    }),
    [kindCounts],
  );
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
  const searchMatches = useMemo(() => {
    if (!normalizedQuery) return [];
    return (snapshot?.nodes ?? [])
      .filter((node) => (
        node.label.toLocaleLowerCase().includes(normalizedQuery)
        || node.id.toLocaleLowerCase().includes(normalizedQuery)
      ))
      .sort((left, right) => {
        const leftStarts = left.label.toLocaleLowerCase().startsWith(normalizedQuery);
        const rightStarts = right.label.toLocaleLowerCase().startsWith(normalizedQuery);
        return Number(rightStarts) - Number(leftStarts) || left.label.localeCompare(right.label);
      });
  }, [normalizedQuery, snapshot]);
  const searchMatchIds = useMemo(
    () => new Set(searchMatches.map((node) => node.id)),
    [searchMatches],
  );
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) ?? null : null;
  const selectedConnections = useMemo<NodeConnection[]>(() => {
    if (!selectedNodeId) return [];
    return (snapshot?.relationships ?? []).flatMap((relationship) => {
      const outgoing = relationship.source === selectedNodeId;
      const incoming = relationship.target === selectedNodeId;
      if (!outgoing && !incoming) return [];
      const neighborId = outgoing ? relationship.target : relationship.source;
      const neighbor = nodeById.get(neighborId);
      return neighbor ? [{ relationship, neighbor, outgoing }] : [];
    }).sort((left, right) => (
      left.relationship.kind.localeCompare(right.relationship.kind)
      || left.neighbor.label.localeCompare(right.neighbor.label)
    ));
  }, [nodeById, selectedNodeId, snapshot]);
  const emphasizedNodeIds = useMemo(() => {
    const ids = new Set<string>();
    if (selectedNodeId) ids.add(selectedNodeId);
    for (const connection of selectedConnections) ids.add(connection.neighbor.id);
    return ids;
  }, [selectedConnections, selectedNodeId]);

  const isNodeVisible = useCallback(
    (node: VisualNode) => (
      visibleKinds.includes(node.kind)
      && (!focusMode || !selectedNodeId || emphasizedNodeIds.has(String(node.id)))
    ),
    [emphasizedNodeIds, focusMode, selectedNodeId, visibleKinds],
  );
  const isLinkVisible = useCallback((link: VisualLink) => {
    const source = endpointId(link.source);
    const target = endpointId(link.target);
    if (!source || !target) return false;
    const sourceNode = nodeById.get(source);
    const targetNode = nodeById.get(target);
    const kindsVisible = Boolean(
      sourceNode
      && targetNode
      && visibleKinds.includes(sourceNode.kind)
      && visibleKinds.includes(targetNode.kind),
    );
    if (!kindsVisible) return false;
    return !focusMode || !selectedNodeId || source === selectedNodeId || target === selectedNodeId;
  }, [focusMode, nodeById, selectedNodeId, visibleKinds]);
  const isEmphasizedLink = useCallback((link: VisualLink) => {
    if (!selectedNodeId && !hoveredNodeId) return false;
    const focusId = selectedNodeId ?? hoveredNodeId;
    return endpointId(link.source) === focusId || endpointId(link.target) === focusId;
  }, [hoveredNodeId, selectedNodeId]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    const updateDimensions = () => {
      setDimensions({
        width: Math.max(320, Math.round(stage.clientWidth)),
        height: Math.max(510, Math.round(stage.clientHeight)),
      });
    };
    updateDimensions();
    const observer = new ResizeObserver(updateDimensions);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [hasData]);

  useEffect(() => {
    hasFittedRef.current = false;
    forcesConfiguredRef.current = false;
    setSelectedNodeId(null);
    setFocusMode(false);
  }, [snapshot]);

  useEffect(() => {
    if (selectedNode && !visibleKinds.includes(selectedNode.kind)) {
      setSelectedNodeId(null);
      setFocusMode(false);
    }
  }, [selectedNode, visibleKinds]);

  const fitGraph = useCallback((duration = 500) => {
    graphRef.current?.zoomToFit(duration, 54, (rawNode) => isNodeVisible(rawNode as VisualNode));
  }, [isNodeVisible]);

  useEffect(() => {
    if (!focusMode || !selectedNodeId) return undefined;
    const timer = window.setTimeout(() => fitGraph(500), 40);
    return () => window.clearTimeout(timer);
  }, [fitGraph, focusMode, selectedNodeId]);

  const selectAndFocusNode = useCallback((nodeId: string) => {
    const graphNode = graphData.nodes.find((node) => node.id === nodeId);
    const sourceNode = nodeById.get(nodeId);
    if (!graphNode || !sourceNode) return;
    if (!visibleKinds.includes(sourceNode.kind)) {
      setVisibleKinds((current) => [...current, sourceNode.kind]);
    }
    setSelectedNodeId(nodeId);
    setSearchQuery("");
    window.setTimeout(() => {
      if (typeof graphNode.x !== "number" || typeof graphNode.y !== "number") return;
      graphRef.current?.centerAt(graphNode.x, graphNode.y, 650);
      graphRef.current?.zoom(Math.max(graphRef.current.zoom(), 2.6), 650);
    }, 0);
  }, [graphData.nodes, nodeById, visibleKinds]);

  function toggleKind(kind: string) {
    setVisibleKinds((current) => {
      if (!current.includes(kind)) return [...current, kind];
      if (current.length === 1) return current;
      return current.filter((value) => value !== kind);
    });
  }

  function zoomBy(factor: number) {
    const graph = graphRef.current;
    if (!graph) return;
    graph.zoom(Math.min(8, Math.max(0.15, graph.zoom() * factor)), 220);
  }

  function resetLayout() {
    for (const node of graphData.nodes) {
      node.fx = undefined;
      node.fy = undefined;
    }
    hasFittedRef.current = false;
    graphRef.current?.d3ReheatSimulation();
  }

  function configureForces() {
    const graph = graphRef.current;
    if (!graph || forcesConfiguredRef.current) return;
    const charge = graph.d3Force("charge");
    charge?.strength?.(-24);
    charge?.distanceMax?.(260);
    const link = graph.d3Force("link");
    link?.distance?.(48);
    link?.iterations?.(1);
    graph.d3Force("kind-cluster", createKindClusterForce());
    forcesConfiguredRef.current = true;
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" && searchMatches[0]) {
      event.preventDefault();
      selectAndFocusNode(searchMatches[0].id);
    }
    if (event.key === "Escape") setSearchQuery("");
  }

  function drawNode(
    rawNode: NodeObject,
    context: CanvasRenderingContext2D,
    globalScale: number,
  ) {
    const node = rawNode as VisualNode;
    if (typeof node.x !== "number" || typeof node.y !== "number") return;
    const meta = KIND_META[node.kind] ?? { label: node.kind, color: "#63726d", soft: "#e6ebe8" };
    const selected = node.id === selectedNodeId;
    const hovered = node.id === hoveredNodeId;
    const matched = normalizedQuery.length > 0 && searchMatchIds.has(String(node.id));
    const dimmed = Boolean(selectedNodeId && !emphasizedNodeIds.has(String(node.id)));
    const radius = (5 + Math.min(4, Math.sqrt(node.degree) * 0.42))
      / Math.max(0.48, Math.sqrt(globalScale));

    context.save();
    context.globalAlpha = dimmed ? 0.18 : 1;
    if (selected || matched) {
      context.beginPath();
      context.arc(node.x, node.y, radius + 4 / globalScale, 0, 2 * Math.PI);
      context.fillStyle = selected ? "rgba(32, 91, 78, .18)" : "rgba(211, 145, 38, .2)";
      context.fill();
    }
    context.beginPath();
    context.arc(node.x, node.y, radius, 0, 2 * Math.PI);
    context.fillStyle = meta.soft;
    context.fill();
    context.lineWidth = (selected ? 2.2 : 1.1) / globalScale;
    context.strokeStyle = meta.color;
    context.stroke();

    const showLabel = selected || hovered || matched || globalScale >= 2.15;
    if (showLabel) {
      const label = truncateLabel(node.label, selected || hovered ? 28 : 16);
      const fontSize = 11.5 / globalScale;
      context.font = `650 ${fontSize}px system-ui, sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      const textWidth = context.measureText(label).width;
      const labelWidth = textWidth + 10 / globalScale;
      const labelHeight = 19 / globalScale;
      const labelY = node.y + radius + 12 / globalScale;
      context.fillStyle = "rgba(255, 255, 253, .94)";
      context.strokeStyle = selected ? meta.color : "rgba(117, 132, 126, .38)";
      context.lineWidth = 0.8 / globalScale;
      roundedRect(
        context,
        node.x - labelWidth / 2,
        labelY - labelHeight / 2,
        labelWidth,
        labelHeight,
        4 / globalScale,
      );
      context.fillStyle = "#26332f";
      context.fillText(label, node.x, labelY + 0.4 / globalScale);
    }
    context.restore();
  }

  function paintNodePointerArea(
    rawNode: NodeObject,
    color: string,
    context: CanvasRenderingContext2D,
    globalScale: number,
  ) {
    const node = rawNode as VisualNode;
    if (typeof node.x !== "number" || typeof node.y !== "number") return;
    const radius = (8 + Math.min(4, Math.sqrt(node.degree) * 0.42))
      / Math.max(0.48, Math.sqrt(globalScale));
    context.beginPath();
    context.arc(node.x, node.y, radius, 0, 2 * Math.PI);
    context.fillStyle = color;
    context.fill();
  }

  function drawLinkLabel(
    rawLink: LinkObject,
    context: CanvasRenderingContext2D,
    globalScale: number,
  ) {
    const link = rawLink as VisualLink;
    if (!isEmphasizedLink(link) || typeof link.source !== "object" || typeof link.target !== "object") {
      return;
    }
    const source = link.source as VisualNode;
    const target = link.target as VisualNode;
    if (
      typeof source.x !== "number"
      || typeof source.y !== "number"
      || typeof target.x !== "number"
      || typeof target.y !== "number"
    ) return;
    const label = RELATION_LABELS[link.kind] ?? link.kind;
    const x = (source.x + target.x) / 2;
    const y = (source.y + target.y) / 2;
    const fontSize = 9.5 / globalScale;
    context.save();
    context.font = `600 ${fontSize}px system-ui, sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    const width = context.measureText(label).width + 7 / globalScale;
    const height = 15 / globalScale;
    context.fillStyle = "rgba(255, 255, 253, .9)";
    context.strokeStyle = "rgba(83, 105, 97, .28)";
    context.lineWidth = 0.6 / globalScale;
    roundedRect(context, x - width / 2, y - height / 2, width, height, 3 / globalScale);
    context.fillStyle = "#52645e";
    context.fillText(label, x, y);
    context.restore();
  }

  return (
    <section className="graph-canvas-card" aria-labelledby="graph-canvas-title">
      <header className="graph-canvas-header">
        <div>
          <span className="graph-heading-icon"><Network size={18} /></span>
          <div>
            <h2 id="graph-canvas-title">规则关系</h2>
            <p>拖拽节点、滚轮缩放；选中节点查看真实 Neo4j 关系</p>
          </div>
        </div>
        <span className="graph-connection-status" data-connected={connected || undefined}>
          <i />{connected ? "已连接" : "连接不可用"}
        </span>
      </header>

      {!hasData ? (
        <div className="graph-stage is-empty">
          <div>
            <span><Database size={26} /></span>
            <strong>
              {snapshotFailed
                ? "真实图谱读取失败"
                : connected ? "Neo4j 图谱为空" : "Neo4j 连接不可用"}
            </strong>
            <p>
              {snapshotFailed
                ? `Neo4j 当前有 ${status?.node_count ?? 0} 个节点，请刷新后重试。`
                : connected
                  ? `${status?.node_count ?? 0} 个节点 · ${status?.relationship_count ?? 0} 条关系 · 数据库 ${status?.database}`
                  : "请确认 Neo4j 服务已经启动，后端连接配置正确。"}
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="graph-browser-toolbar">
            <div className="graph-node-search">
              <Search size={14} />
              <input
                value={searchQuery}
                placeholder="搜索规则、条件、概念或事件"
                aria-label="搜索图谱节点"
                onChange={(event) => setSearchQuery(event.target.value)}
                onKeyDown={handleSearchKeyDown}
              />
              {searchQuery && (
                <button type="button" aria-label="清空搜索" onClick={() => setSearchQuery("")}>
                  <X size={13} />
                </button>
              )}
              {normalizedQuery && (
                <div className="graph-search-results">
                  <small>找到 {searchMatches.length} 个节点</small>
                  {searchMatches.slice(0, 8).map((node) => (
                    <button type="button" key={node.id} onClick={() => selectAndFocusNode(node.id)}>
                      <i style={{ background: KIND_META[node.kind]?.color }} />
                      <span><strong>{node.label}</strong><small>{KIND_META[node.kind]?.label ?? node.kind}</small></span>
                    </button>
                  ))}
                  {searchMatches.length === 0 && <p>没有匹配节点</p>}
                </div>
              )}
            </div>
            <div className="graph-zoom-controls" aria-label="图谱视图控制">
              <button type="button" title="缩小" aria-label="缩小" onClick={() => zoomBy(0.72)}><Minus size={14} /></button>
              <button type="button" title="放大" aria-label="放大" onClick={() => zoomBy(1.38)}><Plus size={14} /></button>
              <button type="button" title="适应画布" aria-label="适应画布" onClick={() => fitGraph()}><Maximize2 size={14} /></button>
              <button type="button" title="重新布局" aria-label="重新布局" onClick={resetLayout}><RotateCcw size={14} /></button>
            </div>
            <div className="graph-kind-filters" aria-label="节点类型筛选">
              {availableKinds.map((kind) => {
                const meta = KIND_META[kind] ?? { label: kind, color: "#63726d", soft: "#e6ebe8" };
                const visible = visibleKinds.includes(kind);
                return (
                  <button
                    type="button"
                    key={kind}
                    aria-pressed={visible}
                    onClick={() => toggleKind(kind)}
                  >
                    <i style={{ background: meta.color }} />{meta.label}<small>{kindCounts.get(kind)}</small>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="graph-stage has-graph" ref={stageRef}>
            <ForceGraph2D
              ref={graphRef}
              width={dimensions.width}
              height={dimensions.height}
              graphData={graphData}
              nodeId="id"
              nodeVisibility={(rawNode) => isNodeVisible(rawNode as VisualNode)}
              nodeCanvasObject={drawNode}
              nodePointerAreaPaint={paintNodePointerArea}
              linkVisibility={(rawLink) => isLinkVisible(rawLink as VisualLink)}
              linkColor={(rawLink) => (
                isEmphasizedLink(rawLink as VisualLink)
                  ? "rgba(53, 102, 88, .88)"
                  : selectedNodeId ? "rgba(112, 128, 121, .035)" : "rgba(102, 121, 113, .18)"
              )}
              linkWidth={(rawLink) => (isEmphasizedLink(rawLink as VisualLink) ? 1.7 : 0.42)}
              linkDirectionalArrowLength={(rawLink) => (
                isEmphasizedLink(rawLink as VisualLink) ? 4.5 : 1.8
              )}
              linkDirectionalArrowRelPos={0.88}
              linkDirectionalArrowColor={(rawLink) => (
                isEmphasizedLink(rawLink as VisualLink)
                  ? "rgba(53, 102, 88, .9)"
                  : "rgba(102, 121, 113, .24)"
              )}
              linkCanvasObjectMode="after"
              linkCanvasObject={drawLinkLabel}
              minZoom={0.08}
              maxZoom={10}
              d3AlphaDecay={0.035}
              d3VelocityDecay={0.34}
              cooldownTicks={220}
              enableNodeDrag
              onNodeHover={(rawNode) => setHoveredNodeId(
                rawNode?.id === undefined ? null : String(rawNode.id),
              )}
              onNodeClick={(rawNode) => selectAndFocusNode(String(rawNode.id))}
              onNodeDragEnd={(rawNode) => {
                rawNode.fx = rawNode.x;
                rawNode.fy = rawNode.y;
              }}
              onBackgroundClick={() => {
                setSelectedNodeId(null);
                setFocusMode(false);
              }}
              onEngineTick={configureForces}
              onEngineStop={() => {
                if (hasFittedRef.current) return;
                hasFittedRef.current = true;
                fitGraph(650);
              }}
            />

            <div className="graph-browser-hint">
              {snapshot?.nodes.length.toLocaleString()} 个节点 · {snapshot?.relationships.length.toLocaleString()} 条关系
            </div>

            {selectedNode && (
              <aside className="graph-node-detail" aria-label="节点关系详情">
                <header>
                  <div>
                    <span style={{ color: KIND_META[selectedNode.kind]?.color }}>
                      {KIND_META[selectedNode.kind]?.label ?? selectedNode.kind}
                    </span>
                    <strong>{selectedNode.label}</strong>
                  </div>
                  <button type="button" aria-label="关闭节点详情" onClick={() => setSelectedNodeId(null)}>
                    <X size={15} />
                  </button>
                </header>
                <div className="graph-node-detail-meta">
                  <span>{selectedConnections.length} 条直接关系</span>
                  <div>
                    <button
                      type="button"
                      data-active={focusMode || undefined}
                      onClick={() => setFocusMode((current) => !current)}
                    >
                      <Network size={12} />{focusMode ? "显示全图" : "只看关联"}
                    </button>
                    <button type="button" onClick={() => selectAndFocusNode(selectedNode.id)}>
                      <LocateFixed size={12} />定位
                    </button>
                  </div>
                </div>
                <code title={selectedNode.id}>{selectedNode.id}</code>
                <div className="graph-node-relations">
                  {selectedConnections.slice(0, 80).map((connection) => (
                    <button
                      type="button"
                      key={connection.relationship.id}
                      onClick={() => selectAndFocusNode(connection.neighbor.id)}
                    >
                      <span>
                        {connection.outgoing ? "→" : "←"}
                        {RELATION_LABELS[connection.relationship.kind] ?? connection.relationship.kind}
                      </span>
                      <strong>{connection.neighbor.label}</strong>
                      <small>{KIND_META[connection.neighbor.kind]?.label ?? connection.neighbor.kind}</small>
                    </button>
                  ))}
                  {selectedConnections.length === 0 && <p>这个节点还没有直接关系。</p>}
                  {selectedConnections.length > 80 && <p>仅显示前 80 条直接关系。</p>}
                </div>
              </aside>
            )}
          </div>
        </>
      )}
    </section>
  );
}
