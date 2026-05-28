// TASK-PE-04 — admin landing page.
//
// Five overview cards (tags / users / apps / personal-KB pending / external KB
// configs) wired to GET /api/v1/ncmu/admin/stats via useAdminStats, plus a
// 工具 section with two DSL Link cards (the DSL import / export routes are
// added by PE-10; the cards render before the routes exist so the navigation
// surface stays stable through the batch).
//
// The "pending Personal KB" card highlights when count > 0 to draw the
// admin's attention (mirrors plan §3 line 780). data-highlight is exposed
// on the testid root so vitest can lock the visual flag without coupling
// to antd's border colour token.

import { Alert, Card, Col, Row, Skeleton, Space } from "antd";
import { Link } from "react-router-dom";
import { useAdminStats } from "@/hooks/useAdminStats";

interface StatCardProps {
  testid: string;
  to: string;
  title: string;
  count: number;
  unit: string;
  highlight?: boolean;
}

function StatCard({ testid, to, title, count, unit, highlight = false }: StatCardProps) {
  return (
    <Col xs={24} sm={12} md={8} lg={6} data-testid={testid} data-highlight={highlight}>
      <Link to={to}>
        <Card
          hoverable
          style={highlight ? { border: "2px solid #faad14" } : undefined}
        >
          <Card.Meta title={title} description={`${count} ${unit}`} />
        </Card>
      </Link>
    </Col>
  );
}

interface ToolCardProps {
  testid: string;
  to: string;
  title: string;
}

function ToolCard({ testid, to, title }: ToolCardProps) {
  return (
    <Col xs={24} sm={12} md={8} lg={6} data-testid={testid}>
      <Link to={to}>
        <Card hoverable>
          <Card.Meta title={title} />
        </Card>
      </Link>
    </Col>
  );
}

export function AdminHomePage() {
  const { data, isLoading, isError, error } = useAdminStats();

  if (isError) {
    return (
      <Alert
        type="error"
        message="管理后台概览加载失败"
        description={error?.message ?? "未知错误"}
        showIcon
      />
    );
  }
  if (isLoading || !data) {
    return <Skeleton active />;
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Row gutter={[16, 16]}>
        <StatCard
          testid="admin-stat-tags"
          to="/admin/tags"
          title="标签管理"
          count={data.tag_count}
          unit="个标签"
        />
        <StatCard
          testid="admin-stat-users"
          to="/admin/users"
          title="用户管理"
          count={data.user_count}
          unit="个用户"
        />
        <StatCard
          testid="admin-stat-apps"
          to="/admin/apps"
          title="App 管理"
          count={data.app_count}
          unit="个 App"
        />
        <StatCard
          testid="admin-stat-personal-kb"
          to="/admin/personal-kb"
          title="个人 KB"
          count={data.pending_personal_kb_count}
          unit="个待办"
          highlight={data.pending_personal_kb_count > 0}
        />
        <StatCard
          testid="admin-stat-kb-configs"
          to="/admin/kb-configs"
          title="外部 KB"
          count={data.external_kb_config_count}
          unit="个配置"
        />
      </Row>
      <div>
        <h3 style={{ marginBottom: 16 }}>工具</h3>
        <Row gutter={[16, 16]}>
          <ToolCard
            testid="admin-tool-dsl-import"
            to="/admin/apps/dsl-import"
            title="DSL 导入"
          />
          <ToolCard
            testid="admin-tool-dsl-export"
            to="/admin/apps/dsl-export"
            title="DSL 导出"
          />
        </Row>
      </div>
    </Space>
  );
}

export default AdminHomePage;
