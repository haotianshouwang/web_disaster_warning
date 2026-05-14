(() => {
/**
 * 文件职责：通知中心视图，负责通知列表展示、已读状态操作与手动刷新交互。
 */

const { Box, Typography, Button, Chip } = MaterialUI;

const NOTIFICATION_TYPE_META = {
    UPDATE: { label: '更新通知', color: 'info' },
    BUGFIX: { label: '修复通知', color: 'success' },
    NOTICE: { label: '公告通知', color: 'warning' },
    SECURITY: { label: '安全通知', color: 'error' },
    TEST: { label: '测试通知', color: 'default' },
};

const NOTIFICATION_TYPE_ALIASES = {
    INFO: 'NOTICE',
};

function resolveNotificationTypeMeta(type) {
    const normalizedType = String(type || '').trim().toUpperCase();
    const canonicalType = NOTIFICATION_TYPE_ALIASES[normalizedType] || normalizedType;
    return NOTIFICATION_TYPE_META[canonicalType] || { label: '其他通知', color: 'default' };
}

function renderNotificationContent(item) {
    const markdownUtil = window.MarkdownRenderUtil;
    const content = markdownUtil
        ? markdownUtil.normalizeMarkdownContent(item?.content || '--')
        : String(item?.content || '--').replace(/\\n/g, '\n');
    const format = String(item?.content_format || 'text').trim().toLowerCase();
    const shouldRenderMarkdown = Boolean(markdownUtil) && (format === 'markdown' || markdownUtil.isProbablyMarkdown(content));

    if (!shouldRenderMarkdown) {
        return (
            <Typography variant="body2" className="notification-feed-content-text notification-feed-content-text-plain" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.85 }}>
                {content}
            </Typography>
        );
    }

    return (
        <Box
            className="notification-feed-content-text notification-md"
            dangerouslySetInnerHTML={{ __html: markdownUtil.renderMarkdownToHtml(content) }}
        />
    );
}

function NotificationsView({ onRefresh }) {
    const { state, dispatch, refreshData } = useAppContext();
    const api = useApi();
    const notifications = Array.isArray(state.notifications) ? state.notifications : [];
    const notificationsMeta = state.notificationsMeta || { unread_count: 0, last_sync_at: '', total_count: 0 };
    const displayTimezone = state.config?.displayTimezone || 'UTC+8';
    const formatNotificationTime = (value) => formatTimeWithZone(value, displayTimezone, true);
    const [busyAction, setBusyAction] = React.useState('');

    const refreshFromServer = async () => {
        setBusyAction('refresh');
        try {
            const payload = await api.refreshNotifications();
            dispatch({ type: 'SET_NOTIFICATIONS', payload: payload.items || [] });
            dispatch({ type: 'SET_NOTIFICATIONS_META', payload: payload.meta || null });
            if (typeof onRefresh === 'function') {
                await onRefresh();
            } else if (typeof refreshData === 'function') {
                await refreshData();
            }
        } catch (e) {
            console.error(e);
        } finally {
            setBusyAction('');
        }
    };

    const markOneAsRead = async (id) => {
        setBusyAction(`read-${id}`);
        try {
            const payload = await api.readNotification(id);
            dispatch({ type: 'SET_NOTIFICATIONS_META', payload: payload.meta || null });
            dispatch({
                type: 'SET_NOTIFICATIONS',
                payload: notifications.map((item) => Number(item.id) === Number(id) ? { ...item, _read: true } : item),
            });
        } catch (e) {
            console.error(e);
        } finally {
            setBusyAction('');
        }
    };

    const markAllAsRead = async () => {
        setBusyAction('read-all');
        try {
            const payload = await api.readAllNotifications();
            dispatch({ type: 'SET_NOTIFICATIONS_META', payload: payload.meta || null });
            dispatch({ type: 'SET_NOTIFICATIONS', payload: notifications.map((item) => ({ ...item, _read: true })) });
        } catch (e) {
            console.error(e);
        } finally {
            setBusyAction('');
        }
    };

    React.useEffect(() => {
        api.getNotifications()
            .then((payload) => {
                dispatch({ type: 'SET_NOTIFICATIONS', payload: payload.items || [] });
                dispatch({ type: 'SET_NOTIFICATIONS_META', payload: payload.meta || null });
            })
            .catch((e) => console.error('Failed to load notifications:', e));
    }, []);

    return (
        <Box className="notifications-view">
            <div className="card notifications-hero-card">
                <Box className="notifications-header-row">
                    <div className="notifications-header-main">
                        <Box className="notifications-title-row">
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, flexWrap: 'wrap' }}>
                                <Typography variant="h6" sx={{ fontWeight: 800 }}>
                                    {`通知中心 (当前共 ${notificationsMeta.total_count || notifications.length} 条通知)`}
                                </Typography>
                                <Chip
                                    label={Number(notificationsMeta.unread_count ?? 0) > 0 ? `未读 ${Number(notificationsMeta.unread_count ?? 0)} 条` : '全部已读'}
                                    size="small"
                                    color={Number(notificationsMeta.unread_count ?? 0) > 0 ? 'warning' : 'success'}
                                    variant="outlined"
                                />
                            </Box>
                            <div className="notifications-inline-meta">
                                <div className="notifications-inline-meta-item is-pill">
                                    <span className="notifications-inline-meta-label">未读通知</span>
                                    <strong>{Number(notificationsMeta.unread_count ?? 0)}</strong>
                                </div>
                                <div className="notifications-inline-meta-item is-pill">
                                    <span className="notifications-inline-meta-label">上次同步</span>
                                    <strong>{notificationsMeta.last_sync_at ? formatNotificationTime(notificationsMeta.last_sync_at) : '--'}</strong>
                                </div>
                            </div>
                        </Box>
                        <Typography variant="body2" color="text.secondary">
                            用于接收插件更新、修复说明、注意事项等官方通知。
                        </Typography>
                    </div>
                    <Box className="notifications-actions-row">
                        <Button variant="outlined" onClick={markAllAsRead} disabled={busyAction === 'read-all' || notifications.length === 0} sx={{ borderRadius: 3 }}>
                            全部已读
                        </Button>
                        <Button variant="contained" onClick={refreshFromServer} disabled={busyAction === 'refresh'} startIcon={<span>🔄</span>} sx={{ borderRadius: 3, boxShadow: 'none', px: 2.25 }}>
                            立即同步
                        </Button>
                    </Box>
                </Box>
            </div>

            {notifications.length === 0 ? (
                <div className="card notifications-empty-card">
                    <div className="notifications-empty-icon">🔔</div>
                    <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>暂无通知</Typography>
                    <Typography variant="body1" color="text.secondary">当前还没有可展示的系统通知。</Typography>
                </div>
            ) : (
                <div className="notifications-feed-list">
                    {notifications.map((item) => {
                        const meta = resolveNotificationTypeMeta(item.type);
                        const isRead = Boolean(item._read) || false;
                        return (
                            <div className={`card notification-feed-card ${isRead ? 'is-read' : 'is-urgent'}`} key={item.id}>
                                <div className="notification-feed-card-top">
                                    <div className="notification-feed-heading-group">
                                        <Typography variant="body1" className="notification-feed-title">{item.title || '--'}</Typography>
                                        <Chip className="notification-feed-type-chip" label={meta.label} size="small" color={meta.color} variant="outlined" />
                                    </div>
                                    <div className="notification-feed-side notification-feed-meta-inline">
                                        <span className={`notification-feed-status-pill ${isRead ? 'is-read' : 'is-unread'}`}>{isRead ? '已读' : '未读'}</span>
                                        <Typography variant="caption" className="mono notification-feed-time">
                                            {formatNotificationTime(item.created_at)}
                                        </Typography>
                                    </div>
                                </div>
                                <div className="notification-feed-content-panel">
                                    {renderNotificationContent(item)}
                                    <Box className="notification-feed-actions notification-feed-actions-inside">
                                        <Button variant="outlined" size="small" disabled={isRead || busyAction === `read-${item.id}`} onClick={() => markOneAsRead(item.id)} sx={{ borderRadius: 999, minWidth: 112, boxShadow: 'none' }}>
                                            {isRead ? '已读' : '确认已读'}
                                        </Button>
                                    </Box>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </Box>
    );
}

window.NotificationsView = NotificationsView;
})();
