import { LinkButton } from "../components/ui";
import { EmptyState } from "../components/ui";

export default function NotFoundPage() {
  return (
    <div className="page-enter notfound-wrap">
      <EmptyState
        icon="compass"
        title="这个页面不存在"
        description="链接可能已经失效，或者地址写错了。回到准备页或面试大厅继续。"
        action={
          <div className="row-actions">
            <LinkButton to="/" variant="primary" iconLeft="spark">
              准备一场面试
            </LinkButton>
            <LinkButton to="/hall" variant="ghost" iconLeft="layers">
              面试大厅
            </LinkButton>
          </div>
        }
      />
    </div>
  );
}
