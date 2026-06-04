import type { ReactNode } from "react";
import { Button, Layout, Menu } from "antd";
import {
  AppstoreOutlined,
  BookOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DownloadOutlined,
  HomeOutlined,
  TagOutlined,
  TeamOutlined,
  DatabaseOutlined,
} from "@ant-design/icons";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { clearAuth, getUser } from "@/lib/auth";

const { Header, Sider, Content } = Layout;

export interface AdminLayoutProps {
  children: ReactNode;
}

// TASK-PE-01: admin-facing antd Layout (Header + Sider + Content).
// Sider lists 8 internal admin routes + 2 external links (Dify / FastGPT)
// at the bottom that open in a new tab. Mirrors EmployeeLayout structure;
// uses module-level clearAuth/getUser (no useAuth hook).
//
// External link URLs match Phase 2C deploy convention (NCMU nginx proxies
// /dify and /fastgpt to upstream containers). Future PE batches that wire
// real per-user SSO into Dify/FastGPT can keep these anchors stable.
export function AdminLayout({ children }: AdminLayoutProps) {
  const user = getUser();
  const location = useLocation();
  const navigate = useNavigate();

  const handleLogout = () => {
    clearAuth();
    navigate("/login", { replace: true });
  };

  const menuItems = [
    { key: "/admin", icon: <HomeOutlined />, label: <Link to="/admin">首页</Link> },
    {
      key: "/admin/monitoring",
      icon: <DashboardOutlined />,
      label: <Link to="/admin/monitoring">监控</Link>,
    },
    {
      key: "/admin/tags",
      icon: <TagOutlined />,
      label: <Link to="/admin/tags">标签管理</Link>,
    },
    {
      key: "/admin/users",
      icon: <TeamOutlined />,
      label: <Link to="/admin/users">用户管理</Link>,
    },
    {
      key: "/admin/apps",
      icon: <AppstoreOutlined />,
      label: <Link to="/admin/apps">App 管理</Link>,
    },
    {
      key: "/admin/kb-configs",
      icon: <DatabaseOutlined />,
      label: <Link to="/admin/kb-configs">外部 KB</Link>,
    },
    {
      key: "/admin/personal-kb",
      icon: <BookOutlined />,
      label: <Link to="/admin/personal-kb">个人 KB</Link>,
    },
    {
      key: "/admin/apps/dsl-import",
      icon: <CloudUploadOutlined />,
      label: <Link to="/admin/apps/dsl-import">DSL 导入</Link>,
    },
    {
      key: "/admin/apps/dsl-export",
      icon: <DownloadOutlined />,
      label: <Link to="/admin/apps/dsl-export">DSL 导出</Link>,
    },
  ];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          paddingInline: 24,
        }}
      >
        <Link to="/admin" style={{ color: "#fff", fontSize: 16, fontWeight: 500 }}>
          NCMU AI 平台 — 管理后台
        </Link>
        <div style={{ color: "#fff", display: "flex", alignItems: "center", gap: 12 }}>
          <span data-testid="role-badge">admin</span>
          <span data-testid="user-name">{user?.name ?? ""}</span>
          <Button type="link" onClick={handleLogout} style={{ color: "#fff" }}>
            退出
          </Button>
        </div>
      </Header>
      <Layout>
        <Sider
          width={200}
          theme="light"
          style={{ display: "flex", flexDirection: "column" }}
        >
          <Menu mode="inline" selectedKeys={[location.pathname]} items={menuItems} />
          <div
            style={{
              marginTop: "auto",
              padding: 16,
              borderTop: "1px solid #f0f0f0",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <a href="/dify" target="_blank" rel="noopener noreferrer">
              跳 Dify ↗
            </a>
            <a href="/fastgpt" target="_blank" rel="noopener noreferrer">
              跳 FastGPT ↗
            </a>
          </div>
        </Sider>
        <Content style={{ padding: 24, background: "#fff" }}>{children}</Content>
      </Layout>
    </Layout>
  );
}

export default AdminLayout;
