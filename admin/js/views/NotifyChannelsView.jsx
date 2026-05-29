const { Box, Typography, Switch, TextField, Button, Card, CardContent, Divider, CircularProgress, FormControlLabel } = MaterialUI;
const { useState, useEffect } = React;

function EmailCard() {
    const [cfg, setCfg] = useState(null);
    const [editing, setEditing] = useState({});
    const [testing, setTesting] = useState(false);
    const [testMsg, setTestMsg] = useState('');
    const { showToast } = useToast();

    useEffect(() => {
        window.NotifyChannelsApi.get('email').then(d => {
            const e = { ...d };
            // 兼容旧 receiver_emails
            if (!e.targets || !Array.isArray(e.targets) || e.targets.length === 0) {
                if (e.receiver_emails) {
                    e.targets = e.receiver_emails.split(',').map(em => ({ email: em.trim(), enabled: true })).filter(t => t.email);
                } else {
                    e.targets = [{ email: '', enabled: true }];
                }
            }
            if (!e.filter_types) e.filter_types = { earthquake: true, weather: true, tsunami: true };
            setCfg(e); setEditing({ ...e });
        }).catch(() => showToast('加载失败', 'error'));
    }, []);

    if (!cfg) return null;
    const enabled = cfg.enabled;

    const addTarget = () => {
        setEditing({ ...editing, targets: [...editing.targets, { email: '', enabled: true }] });
    };
    const removeTarget = (idx) => () => {
        if (editing.targets.length <= 1) return;
        setEditing({ ...editing, targets: editing.targets.filter((_, i) => i !== idx) });
    };
    const updateTarget = (idx, key) => (e) => {
        const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        const copy = [...editing.targets];
        copy[idx] = { ...copy[idx], [key]: v };
        setEditing({ ...editing, targets: copy });
    };
    const updateFilter = (key) => (e) => {
        setEditing({ ...editing, filter_types: { ...editing.filter_types, [key]: e.target.checked } });
    };

    return (
        <Card className="card" style={{ marginBottom: 16 }}>
            <CardContent>
                <Box display="flex" alignItems="center" justifyContent="space-between" mb={1}>
                    <Typography variant="h6">📧 邮件通知</Typography>
                    <Box display="flex" alignItems="center" gap={1}>
                        <Typography variant="body2">{enabled ? '已启用' : '已禁用'}</Typography>
                        <Switch checked={enabled} onChange={toggle(cfg, setCfg, editing, setEditing, 'email', showToast)} color="primary" />
                    </Box>
                </Box>
                <Divider sx={{ mb: 2 }} />
                <TextField label="SMTP 服务器" value={editing.smtp_host || ''} onChange={f('smtp_host', editing, setEditing)} fullWidth margin="dense" size="small" disabled={!enabled} helperText="如 smtp.qq.com" />
                <TextField label="端口" type="number" value={editing.smtp_port || ''} onChange={f('smtp_port', editing, setEditing)} fullWidth margin="dense" size="small" disabled={!enabled} helperText="SSL:465 TLS:587" />
                <TextField label="发件人邮箱" value={editing.sender_email || ''} onChange={f('sender_email', editing, setEditing)} fullWidth margin="dense" size="small" disabled={!enabled} />
                <TextField label="SMTP 授权码" value={editing.auth_code || ''} onChange={f('auth_code', editing, setEditing)} fullWidth margin="dense" size="small" disabled={!enabled} type="password" helperText="邮箱设置中生成，非登录密码" />
                <TextField label="发件人名称" value={editing.sender_name || '灾害预警'} onChange={f('sender_name', editing, setEditing)} fullWidth margin="dense" size="small" disabled={!enabled} helperText="邮件中显示的发件人名" />

                <Typography variant="subtitle2" mt={2}>事件推送过滤</Typography>
                <Box display="flex" gap={3} mt={1} mb={2}>
                    <FormControlLabel control={<Switch checked={editing.filter_types?.earthquake !== false} onChange={updateFilter('earthquake')} color="error" />} label="地震 🌍" />
                    <FormControlLabel control={<Switch checked={editing.filter_types?.weather !== false} onChange={updateFilter('weather')} color="warning" />} label="气象 ⛈️" />
                    <FormControlLabel control={<Switch checked={editing.filter_types?.tsunami !== false} onChange={updateFilter('tsunami')} color="info" />} label="海啸 🌊" />
                </Box>

                <Typography variant="subtitle2" mt={2}>收件人列表</Typography>
                {editing.targets.map((t, idx) => (
                    <Box key={idx} display="flex" gap={1} alignItems="center" mt={1}>
                        <TextField value={t.email || ''} onChange={updateTarget(idx, 'email')} size="small" sx={{ flex: 1 }} placeholder="user@qq.com" />
                        <FormControlLabel control={<Switch checked={t.enabled !== false} onChange={updateTarget(idx, 'enabled')} size="small" />} label="" />
                        <Button size="small" color="error" onClick={removeTarget(idx)} disabled={editing.targets.length <= 1}>✕</Button>
                    </Box>
                ))}
                <Button size="small" variant="outlined" onClick={addTarget} sx={{ mt: 1 }}>+ 添加收件人</Button>

                <Box display="flex" gap={1} mt={2}>
                    <Button variant="contained" onClick={() => save('email', editing, setCfg, showToast)} disabled={!enabled} size="small">保存</Button>
                    <Button variant="outlined" onClick={() => doTest('email', setTesting, setTestMsg, showToast, editing)} disabled={!enabled || testing} size="small">
                        {testing ? <CircularProgress size={16} /> : '发送测试邮件'}
                    </Button>
                </Box>
                {testMsg && <Typography variant="body2" sx={{ mt: 1, color: testMsg.startsWith('✅') ? 'green' : 'red' }}>{testMsg}</Typography>}
            </CardContent>
        </Card>
    );
}

function OneBotCard() {
    const [cfg, setCfg] = useState(null);
    const [editing, setEditing] = useState({});
    const [testing, setTesting] = useState(false);
    const [testMsg, setTestMsg] = useState('');
    const { showToast } = useToast();

    useEffect(() => {
        window.NotifyChannelsApi.get('onebot11').then(d => {
            const e = { ...d };
            // 展开模式
            if (e.http_server_enabled === undefined) e.http_server_enabled = false;
            if (e.http_client_enabled === undefined) e.http_client_enabled = false;
            if (e.ws_server_enabled === undefined) e.ws_server_enabled = false;
            if (e.ws_client_enabled === undefined) e.ws_client_enabled = false;
            // 兼容旧 target_type/target_id
            if (!e.targets || !Array.isArray(e.targets) || e.targets.length === 0) {
                if (e.target_type && e.target_id) {
                    e.targets = [{ type: e.target_type, id: e.target_id, enabled: true }];
                } else {
                    e.targets = [{ type: 'group', id: '', enabled: true }];
                }
            }
            if (!e.filter_types) e.filter_types = { earthquake: true, weather: true, tsunami: true };
            setCfg(e); setEditing({ ...e });
        }).catch(() => showToast('加载失败', 'error'));
    }, []);

    if (!cfg) return null;

    const modes = [
        { key: 'http_server_enabled', label: 'HTTP 服务器', hint: 'NapCat 主动 POST 事件到插件', hostLabel: '监听地址', hostKey: 'http_server_host', portKey: 'http_server_port', hostDefault: '0.0.0.0', portDefault: 5700, pathKey: 'http_server_path', pathDefault: '/onebot', tokenKey: 'http_server_token' },
        { key: 'http_client_enabled', label: 'HTTP 客户端', hint: '插件主动调 NapCat HTTP API', hostLabel: 'API 地址', hostKey: 'http_client_url', hostDefault: 'http://127.0.0.1:3000', isUrl: true, tokenKey: 'http_client_token' },
        { key: 'ws_server_enabled', label: 'WS 服务器', hint: 'NapCat 主动连插件 WebSocket', hostLabel: '监听地址', hostKey: 'ws_server_host', portKey: 'ws_server_port', hostDefault: '0.0.0.0', portDefault: 5701, tokenKey: 'ws_server_token' },
        { key: 'ws_client_enabled', label: 'WS 客户端', hint: '插件主动连 NapCat WebSocket', hostLabel: '连接地址', hostKey: 'ws_client_url', hostDefault: 'ws://127.0.0.1:3001', isUrl: true, tokenKey: 'ws_client_token' },
    ];

    const modeToggle = (key) => () => {
        const next = { ...editing, [key]: !editing[key] };
        setEditing(next);
        window.NotifyChannelsApi.update('onebot11', next)
            .then(d => { const e = { ...d }; if (!e.targets) e.targets = editing.targets; if (!e.filter_types) e.filter_types = editing.filter_types; setCfg(e); })
            .catch(e => showToast(e.message || '保存失败', 'error'));
    };

    // 目标列表操作
    const addTarget = () => {
        const next = { ...editing, targets: [...editing.targets, { type: 'group', id: '', enabled: true }] };
        setEditing(next);
    };
    const removeTarget = (idx) => () => {
        if (editing.targets.length <= 1) return;
        const next = { ...editing, targets: editing.targets.filter((_, i) => i !== idx) };
        setEditing(next);
    };
    const updateTarget = (idx, key) => (e) => {
        const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        const copy = [...editing.targets];
        copy[idx] = { ...copy[idx], [key]: v };
        setEditing({ ...editing, targets: copy });
    };
    const updateFilter = (key) => (e) => {
        const next = { ...editing, filter_types: { ...editing.filter_types, [key]: e.target.checked } };
        setEditing(next);
    };

    return (
        <Card className="card" style={{ marginBottom: 16 }}>
            <CardContent>
                <Typography variant="h6" mb={1}>🤖 OneBot 11 通知</Typography>
                <Divider sx={{ mb: 2 }} />

                {modes.map(m => (
                    <Box key={m.key} mb={2} p={2} border="1px solid var(--md-sys-color-outline-variant, #ddd)" borderRadius={2}>
                        <Box display="flex" alignItems="center" justifyContent="space-between">
                            <Box>
                                <Typography variant="subtitle2">{m.label}</Typography>
                                <Typography variant="caption" color="textSecondary">{m.hint}</Typography>
                            </Box>
                            <Switch checked={editing[m.key] || false} onChange={modeToggle(m.key)} color="primary" />
                        </Box>
                        {editing[m.key] && (
                            <Box mt={1}>
                                {m.isUrl ? (
                                    <TextField label={m.hostLabel} value={editing[m.hostKey] || m.hostDefault} onChange={f(m.hostKey, editing, setEditing)} fullWidth margin="dense" size="small" />
                                ) : (
                                    <Box display="flex" gap={1}>
                                        <TextField label="主机" value={editing[m.hostKey] || m.hostDefault} onChange={f(m.hostKey, editing, setEditing)} margin="dense" size="small" sx={{ flex: 2 }} />
                                        <TextField label="端口" value={editing[m.portKey] || m.portDefault} onChange={f(m.portKey, editing, setEditing)} margin="dense" size="small" type="number" sx={{ flex: 1 }} />
                                        {m.pathKey && <TextField label="路径" value={editing[m.pathKey] || m.pathDefault} onChange={f(m.pathKey, editing, setEditing)} margin="dense" size="small" sx={{ flex: 1 }} />}
                                    </Box>
                                )}
                                <TextField label="Access Token" value={editing[m.tokenKey] || ''} onChange={f(m.tokenKey, editing, setEditing)} fullWidth margin="dense" size="small" type="password" helperText="此模式的鉴权 Token" />
                            </Box>
                        )}
                    </Box>
                ))}

                <Typography variant="subtitle2" mt={2}>事件推送过滤（仅推送勾选的类型）</Typography>
                <Box display="flex" gap={3} mt={1} mb={2}>
                    <FormControlLabel control={<Switch checked={editing.filter_types?.earthquake !== false} onChange={updateFilter('earthquake')} color="error" />} label="地震 🌍" />
                    <FormControlLabel control={<Switch checked={editing.filter_types?.weather !== false} onChange={updateFilter('weather')} color="warning" />} label="气象 ⛈️" />
                    <FormControlLabel control={<Switch checked={editing.filter_types?.tsunami !== false} onChange={updateFilter('tsunami')} color="info" />} label="海啸 🌊" />
                </Box>

                <Typography variant="subtitle2" mt={2}>推送目标（支持多群 + 多私聊）</Typography>
                {editing.targets.map((t, idx) => (
                    <Box key={idx} display="flex" gap={1} alignItems="center" mt={1}>
                        <TextField select value={t.type || 'group'} onChange={updateTarget(idx, 'type')} size="small" sx={{ width: 120 }}
                            SelectProps={{ native: true }}>
                            <option value="group">群聊</option>
                            <option value="private">私聊</option>
                        </TextField>
                        <TextField value={t.id || ''} onChange={updateTarget(idx, 'id')} size="small" sx={{ flex: 1 }} placeholder="群号或QQ号" />
                        <FormControlLabel control={<Switch checked={t.enabled !== false} onChange={updateTarget(idx, 'enabled')} size="small" />} label="" />
                        <Button size="small" color="error" onClick={removeTarget(idx)} disabled={editing.targets.length <= 1}>✕</Button>
                    </Box>
                ))}
                <Button size="small" variant="outlined" onClick={addTarget} sx={{ mt: 1 }}>+ 添加目标</Button>

                <Box display="flex" gap={1} mt={2}>
                    <Button variant="contained" onClick={() => save('onebot11', editing, setCfg, showToast)} size="small">保存全部</Button>
                    <Button variant="outlined" onClick={() => doTest('onebot11', setTesting, setTestMsg, showToast, editing)}
                        disabled={testing} size="small">
                        {testing ? <CircularProgress size={16} /> : '测试连接'}
                    </Button>
                </Box>
                {testMsg && <Typography variant="body2" sx={{ mt: 1, color: testMsg.startsWith('✅') ? 'green' : 'red' }}>{testMsg}</Typography>}
            </CardContent>
        </Card>
    );
}

// 辅助函数
function f(key, editing, setEditing) {
    return (e) => setEditing({ ...editing, [key]: e.target.value });
}
function toggle(cfg, setCfg, editing, setEditing, id, showToast) {
    return () => {
        const next = { ...editing, enabled: !editing.enabled };
        setEditing(next); setCfg(next);
        window.NotifyChannelsApi.update(id, next)
            .then(d => setCfg(d))
            .catch(e => showToast(e.message || '保存', 'error'));
    };
}
function save(id, editing, setCfg, showToast) {
    window.NotifyChannelsApi.update(id, editing)
        .then(d => { setCfg(d); showToast('保存成功', 'success'); })
        .catch(e => showToast(e.message || '保存失败', 'error'));
}
function doTest(id, setTesting, setTestMsg, showToast, editing) {
    setTesting(true); setTestMsg('');
    // 将当前编辑中的配置传给测试接口
    window.NotifyChannelsApi.test(id, editing)
        .then(d => setTestMsg('✅ ' + (d.message || '测试通过')))
        .catch(e => setTestMsg('❌ ' + (e.message || '测试失败')))
        .finally(() => setTesting(false));
}

function NotifyChannelsView() {
    return (
        <Box>
            <EmailCard />
            <OneBotCard />
        </Box>
    );
}
