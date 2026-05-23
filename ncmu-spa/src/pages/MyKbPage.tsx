// TASK-PC3-A — /my-kb 员工 Personal-KB 申请主页.
//
// Composition (plan §4.6 AC#1):
//   - 顶部 [+ 申请新建知识库] 按钮 → opens KbApplicationModal
//   - 5 状态过滤 Segmented (全部 / pending / in_progress / done / rejected / cancelled)
//   - 申请列表 antd List, 每项 ApplicationListItem (status badge + actions)
//   - polling 60s via useMyApplications hook (visibility-aware)
//
// Responsive: outer container max-width 960px + auto margin; inner List
// uses antd Grid breakpoints by default — phone single column, desktop
// avoids horizontal scroll (AC#9).
//
// Sidebar 菜单项 / Layout 推迟 Phase 2D — spec §1.2 r2 INDEP C1 字面.
// Direct-URL access only at /my-kb. RequireAuth route guard is wired in
// routes.tsx (任何登录员工 / 非 admin 也可访问 / requireAdmin=false 默认).

import { useMemo, useState } from "react";
import {
  App as AntdApp,
  Alert,
  Button,
  Empty,
  Layout,
  List,
  Segmented,
  Skeleton,
  Space,
  Typography,
  notification,
} from "antd";
import { useMyApplications } from "@/pages/MyKbPage/hooks/useMyApplications";
import {
  ApplicationListItem,
} from "@/pages/MyKbPage/components/ApplicationListItem";
import {
  KbApplicationModal,
} from "@/pages/MyKbPage/components/KbApplicationModal";
import { ApiError, cancelApplication } from "@/lib/api";
import type { PersonalKbApplicationStatus } from "@/lib/api";

type FilterValue = "all" | PersonalKbApplicationStatus;

const FILTER_OPTIONS: { label: string; value: FilterValue }[] = [
  { label: "全部", value: "all" },
  { label: "待处理", value: "pending" },
  { label: "处理中", value: "in_progress" },
  { label: "已完成", value: "done" },
  { label: "已拒绝", value: "rejected" },
  { label: "已撤回", value: "cancelled" },
];

export function MyKbPage() {
  const { data, isLoading, isError, error, refetch } = useMyApplications();
  const [filter, setFilter] = useState<FilterValue>("all");
  const [modalOpen, setModalOpen] = useState<boolean>(false);

  const filtered = useMemo(() => {
    if (!data) return [];
    if (filter === "all") return data;
    return data.filter((app) => app.status === filter);
  }, [data, filter]);

  const handleCancel = async (id: string) => {
    try {
      await cancelApplication(id);
      notification.success({ message: "已撤回" });
      refetch();
    } catch (err: unknown) {
      const desc =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "未知错误";
      notification.error({ message: "撤回失败", description: desc });
    }
  };

  return (
    <AntdApp>
      <Layout.Content
        data-testid="my-kb-page"
        style={{
          padding: 24,
          maxWidth: 960,
          margin: "0 auto",
          width: "100%",
        }}
      >
        <Space
          direction="horizontal"
          style={{
            justifyContent: "space-between",
            width: "100%",
            marginBottom: 16,
            flexWrap: "wrap",
            gap: 12,
          }}
        >
          <Typography.Title level={3} style={{ margin: 0 }}>
            我的知识库申请
          </Typography.Title>
          <Button
            type="primary"
            onClick={() => setModalOpen(true)}
            data-testid="my-kb-new-button"
          >
            + 申请新建知识库
          </Button>
        </Space>

        <Segmented<FilterValue>
          options={FILTER_OPTIONS}
          value={filter}
          onChange={(v) => setFilter(v as FilterValue)}
          style={{ marginBottom: 16 }}
          data-testid="my-kb-status-filter"
        />

        {isError && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message="加载申请列表失败"
            description={error?.message ?? "未知错误"}
          />
        )}

        {isLoading ? (
          <Skeleton active />
        ) : filtered.length === 0 ? (
          <Empty
            description={
              filter === "all" ? "暂无申请记录" : "该状态下暂无申请"
            }
          />
        ) : (
          <div data-testid="my-kb-application-list">
            <List
              dataSource={filtered}
              renderItem={(app) => (
                <ApplicationListItem
                  key={app.id}
                  app={app}
                  onCancel={handleCancel}
                />
              )}
            />
          </div>
        )}

        <KbApplicationModal
          open={modalOpen}
          onClose={() => setModalOpen(false)}
          onSubmitted={refetch}
        />
      </Layout.Content>
    </AntdApp>
  );
}

export default MyKbPage;
