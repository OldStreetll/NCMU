// TASK-PC3-B — double-column admin detail page (Q7-C).
//
// /admin/personal-kb/applications/:id — left 30% (xs=24 sm=10) is the read-only
// applicant view + file downloads; right 70% (xs=24 sm=14) is the action panel
// (claim → dispatch / reject). Both halves stack on mobile.

import { useNavigate, useParams } from "react-router-dom";
import { Alert, Col, Row, Spin, Typography } from "antd";
import { useAdminApplication } from "./AdminApplicationDetailPage/hooks/useAdminApplication";
import { ApplicationDetailPanel } from "./AdminApplicationDetailPage/components/ApplicationDetailPanel";
import { DispatchFormPanel } from "./AdminApplicationDetailPage/components/DispatchFormPanel";

export function AdminApplicationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, isLoading, error, invalidate } = useAdminApplication(id);

  if (isLoading) {
    return (
      <div
        data-testid="admin-application-detail-loading"
        style={{ display: "flex", justifyContent: "center", padding: 48 }}
      >
        <Spin />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        data-testid="admin-application-detail-error"
        type="error"
        showIcon
        message={`加载失败 HTTP ${error.status}`}
        description={error.message}
        style={{ margin: 24 }}
      />
    );
  }

  if (!data) {
    return (
      <Alert
        data-testid="admin-application-detail-not-found"
        type="warning"
        showIcon
        message="申请不存在"
        style={{ margin: 24 }}
      />
    );
  }

  function backToList() {
    navigate("/admin/personal-kb");
  }

  return (
    <div data-testid="admin-application-detail-page" style={{ padding: 24 }}>
      <Typography.Title level={3} style={{ marginTop: 0 }}>
        申请详情
      </Typography.Title>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={10}>
          <ApplicationDetailPanel detail={data} />
        </Col>
        <Col xs={24} sm={14}>
          <DispatchFormPanel
            detail={data}
            onChanged={invalidate}
            onDispatched={backToList}
            onRejected={backToList}
          />
        </Col>
      </Row>
    </div>
  );
}

export default AdminApplicationDetailPage;
