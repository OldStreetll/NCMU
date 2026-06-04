// TASK-MON-2 — admin monitoring page.
//
// Renders the GET /api/v1/ncmu/admin/monitoring snapshot (MonitoringOut,
// contract frozen by MON-1) in two blocks: 业务运营 / 钉钉集成. MVP snapshot —
// no charting library, just antd Card / Statistic / Row·Col / Progress /
// Badge / Table.
//
// Degradation rules (mirror AdminHomePage Skeleton pattern):
//   - loading → <Skeleton active/>; error → <Alert/>.
//   - avg_kb_processing_seconds === null  → "—".
//   - last_dify_app_sync_at  === null     → "—".
//   - config_readiness 4 health lights: green Badge = true, grey = false.
//     SECURITY: only the boolean readiness is rendered, never any secret value.

import {
  Alert,
  Badge,
  Card,
  Col,
  Descriptions,
  Empty,
  Progress,
  Row,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
} from "antd";
import type {
  ConfigReadiness,
  KbApplicationStatus,
  TagBinding,
} from "@/hooks/useAdminMonitoring";
import { useAdminMonitoring } from "@/hooks/useAdminMonitoring";

const PLACEHOLDER = "—";

// Friendly local datetime (zh-CN) for the snapshot timestamp + last-sync field.
// Mirrors the SPA's existing `new Date(iso).toLocaleString()` convention
// (MyKbPage/components/ApplicationListItem.formatRelative fallback). null →
// "—"; an unparseable string falls back to the raw value rather than "Invalid
// Date".
function formatDateTime(iso: string | null): string {
  if (iso === null) return PLACEHOLDER;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN");
}

interface StatItemProps {
  testid: string;
  title: string;
  value: number | string;
  suffix?: string;
}

// Single antd Statistic in a responsive Col, testid on the Col root so vitest
// can scope a field-level assertion to one metric.
function StatItem({ testid, title, value, suffix }: StatItemProps) {
  return (
    <Col xs={12} sm={8} md={6} data-testid={testid}>
      <Statistic title={title} value={value} suffix={suffix} />
    </Col>
  );
}

// kb_application_status 5-state distribution as labelled Progress bars (percent
// relative to the largest state so the bars stay readable when totals are
// small). The count itself is rendered in the format slot — that is the value
// the test locks, not the percent.
const KB_STATUS_ORDER: ReadonlyArray<[keyof KbApplicationStatus, string]> = [
  ["pending", "待处理"],
  ["in_progress", "处理中"],
  ["done", "已完成"],
  ["rejected", "已拒绝"],
  ["cancelled", "已取消"],
];

function KbStatusDistribution({ status }: { status: KbApplicationStatus }) {
  const max = Math.max(1, ...KB_STATUS_ORDER.map(([k]) => status[k]));
  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      {KB_STATUS_ORDER.map(([key, label]) => {
        const count = status[key];
        return (
          <div
            key={key}
            data-testid={`mon-kb-status-${key}`}
            style={{ display: "flex", alignItems: "center", gap: 12 }}
          >
            <span style={{ width: 56, flex: "0 0 auto" }}>{label}</span>
            <Progress
              style={{ flex: 1, marginBottom: 0 }}
              percent={Math.round((count / max) * 100)}
              format={() => count}
            />
          </div>
        );
      })}
    </Space>
  );
}

const READINESS_ORDER: ReadonlyArray<[keyof ConfigReadiness, string]> = [
  ["app_key", "App Key"],
  ["app_secret", "App Secret"],
  ["corp_id", "Corp ID"],
  ["login_redirect_uri", "登录回调地址"],
];

// Health light: green Badge = configured, grey = missing. Renders ONLY the
// boolean state + label — never the secret value behind it.
function ReadinessLights({ readiness }: { readiness: ConfigReadiness }) {
  return (
    <Space direction="vertical" size={8}>
      {READINESS_ORDER.map(([key, label]) => {
        const ok = readiness[key];
        return (
          <div key={key} data-testid={`mon-readiness-${key}`} data-ok={ok}>
            <Badge status={ok ? "success" : "default"} text={label} />
          </div>
        );
      })}
    </Space>
  );
}

function TagBindingsTable({ bindings }: { bindings: TagBinding[] }) {
  if (bindings.length === 0) {
    return <Empty description="暂无标签绑定" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }
  return (
    <Table<TagBinding>
      rowKey="tag_id"
      size="small"
      pagination={false}
      dataSource={bindings}
      columns={[
        { title: "标签", dataIndex: "name", key: "name" },
        { title: "App 数", dataIndex: "app_count", key: "app_count" },
        { title: "用户数", dataIndex: "user_count", key: "user_count" },
      ]}
    />
  );
}

export function AdminMonitoringPage() {
  const { data, isLoading, isError, error } = useAdminMonitoring();

  if (isError) {
    return (
      <Alert
        type="error"
        message="监控数据加载失败"
        description={error?.message ?? "未知错误"}
        showIcon
      />
    );
  }
  if (isLoading || !data) {
    return <Skeleton active />;
  }

  const { business, dingtalk, generated_at } = data;
  const avgProcessing =
    business.avg_kb_processing_seconds === null
      ? PLACEHOLDER
      : `${Math.round(business.avg_kb_processing_seconds)} 秒`;
  const lastSync = formatDateTime(business.last_dify_app_sync_at);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div data-testid="mon-generated-at" style={{ color: "#888" }}>
        数据生成时间：{formatDateTime(generated_at)}
      </div>

      {/* ── 区块 1：业务运营 ─────────────────────────────────── */}
      <Card title="业务运营" data-testid="mon-block-business">
        <Row gutter={[16, 16]}>
          <StatItem testid="mon-user-total" title="用户总数" value={business.user_total} />
          <StatItem testid="mon-user-active" title="活跃用户" value={business.user_active} />
          <StatItem
            testid="mon-user-dingtalk"
            title="已绑定钉钉"
            value={business.user_with_dingtalk}
          />
          <StatItem
            testid="mon-new-users-7d"
            title="近 7 天新增用户"
            value={business.new_users_7d}
          />
          <StatItem testid="mon-app-total" title="App 总数" value={business.app_total} />
          <StatItem testid="mon-app-active" title="活跃 App" value={business.app_active} />
          <StatItem
            testid="mon-kb-backlog"
            title="KB 待办积压"
            value={business.kb_pending_backlog}
          />
          <StatItem
            testid="mon-new-kb-7d"
            title="近 7 天新增 KB 申请"
            value={business.new_kb_applications_7d}
          />
        </Row>

        <Descriptions
          column={1}
          size="small"
          style={{ marginTop: 16 }}
          items={[
            {
              key: "avg",
              label: "KB 平均处理时长",
              children: <span data-testid="mon-avg-processing">{avgProcessing}</span>,
            },
            {
              key: "sync",
              label: "上次 Dify App 同步",
              children: <span data-testid="mon-last-sync">{lastSync}</span>,
            },
          ]}
        />

        <div style={{ marginTop: 16 }}>
          <h4 style={{ marginBottom: 8 }}>KB 申请状态分布</h4>
          <KbStatusDistribution status={business.kb_application_status} />
        </div>

        <div style={{ marginTop: 16 }} data-testid="mon-tag-bindings">
          <h4 style={{ marginBottom: 8 }}>标签绑定</h4>
          <TagBindingsTable bindings={business.tag_bindings} />
        </div>
      </Card>

      {/* ── 区块 2：钉钉集成 ─────────────────────────────────── */}
      <Card title="钉钉集成" data-testid="mon-block-dingtalk">
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} md={8} data-testid="mon-config-readiness">
            <h4 style={{ marginBottom: 8 }}>配置就绪度</h4>
            <ReadinessLights readiness={dingtalk.config_readiness} />
          </Col>
          <Col xs={24} sm={12} md={16}>
            <Row gutter={[16, 16]}>
              <StatItem
                testid="mon-dept-tag-mapping"
                title="部门-标签映射"
                value={dingtalk.department_tag_mapping_count}
              />
              <StatItem
                testid="mon-synced-accounts"
                title="已同步账号"
                value={dingtalk.synced_account_count}
              />
              <StatItem
                testid="mon-routing-users"
                title="路由影响用户"
                value={dingtalk.routing_affected_user_count}
              />
              <StatItem
                testid="mon-routing-apps"
                title="路由影响 App"
                value={dingtalk.routing_affected_app_count}
              />
              <Col xs={24} data-testid="mon-routing-enabled" data-enabled={dingtalk.tags_routing_enabled}>
                <Space>
                  <span>标签路由：</span>
                  <Tag color={dingtalk.tags_routing_enabled ? "green" : "default"}>
                    {dingtalk.tags_routing_enabled ? "已启用" : "未启用"}
                  </Tag>
                </Space>
              </Col>
            </Row>
          </Col>
        </Row>
      </Card>
    </Space>
  );
}

export default AdminMonitoringPage;
