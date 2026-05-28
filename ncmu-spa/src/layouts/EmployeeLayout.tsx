import type { ReactNode } from "react";
import { Button, Layout, Menu } from "antd";
import { AppstoreOutlined, BookOutlined } from "@ant-design/icons";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { clearAuth, getUser } from "@/lib/auth";

const { Header, Sider, Content } = Layout;

export interface EmployeeLayoutProps {
  children: ReactNode;
}

// TASK-PE-01: employee-facing antd Layout (Header + Sider + Content).
// Header shows brand + 角色徽标 + user name + logout. Sider lists 2 main
// employee landing routes: /staff (我的 App) and /my-kb (个人 KB).
// Logout uses the module-level clearAuth() (sync) to mirror RequireAuth.tsx
// pattern; no useAuth hook (lib/auth.ts is module-scoped).
export function EmployeeLayout({ children }: EmployeeLayoutProps) {
  const user = getUser();
  const location = useLocation();
  const navigate = useNavigate();

  const handleLogout = () => {
    clearAuth();
    navigate("/login", { replace: true });
  };

  const menuItems = [
    {
      key: "/staff",
      icon: <AppstoreOutlined />,
      label: <Link to="/staff">我的 App</Link>,
    },
    {
      key: "/my-kb",
      icon: <BookOutlined />,
      label: <Link to="/my-kb">个人 KB</Link>,
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
        <Link to="/staff" style={{ color: "#fff", fontSize: 16, fontWeight: 500 }}>
          NCMU AI 平台
        </Link>
        <div style={{ color: "#fff", display: "flex", alignItems: "center", gap: 12 }}>
          <span data-testid="role-badge">员工</span>
          <span data-testid="user-name">{user?.name ?? ""}</span>
          <Button type="link" onClick={handleLogout} style={{ color: "#fff" }}>
            退出
          </Button>
        </div>
      </Header>
      <Layout>
        <Sider width={200} theme="light">
          <Menu mode="inline" selectedKeys={[location.pathname]} items={menuItems} />
        </Sider>
        <Content style={{ padding: 24, background: "#fff" }}>{children}</Content>
      </Layout>
    </Layout>
  );
}

export default EmployeeLayout;
