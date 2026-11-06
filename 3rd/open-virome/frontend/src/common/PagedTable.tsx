import * as React from 'react';
import Box from '@mui/material/Box';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import TableSortLabel from '@mui/material/TableSortLabel';
import Paper from '@mui/material/Paper';
import { visuallyHidden } from '@mui/utils';
import ExportButton from '../common/ExportButton.tsx';
import Button from '@mui/material/Button';
import Checkbox from '@mui/material/Checkbox';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import DownloadIcon from '@mui/icons-material/Download';
import BiotechIcon from '@mui/icons-material/Biotech';
import { exportToCSV } from '../common/utils/exportHelpers.ts';

// 平台扩展：Run 编号（SRR/ERR/DRR）才可转入平台「公共数据下载」；
// biosample/bioproject 等标识符不在平台下载管理器支持范围内。
const RUN_RE = /^[SED]RR\d+$/;

// 从一行数据里找出可下载的 Run 编号；找不到返回 null
const rowRun = (row: any, headers: string[]): string | null => {
    for (const h of headers) {
        const v = row ? row[h] : null;
        if (typeof v === 'string' && RUN_RE.test(v)) return v;
    }
    return null;
};

function descendingComparator<T>(a: T, b: T, orderBy: keyof T) {
    if (b[orderBy] < a[orderBy]) {
        return -1;
    }
    if (b[orderBy] > a[orderBy]) {
        return 1;
    }
    return 0;
}

type Order = 'asc' | 'desc';

function getComparator<Key extends keyof any>(
    order: Order,
    orderBy: Key,
): (a: { [key in Key]: number | string }, b: { [key in Key]: number | string }) => number {
    return order === 'desc'
        ? (a, b) => descendingComparator(a, b, orderBy)
        : (a, b) => -descendingComparator(a, b, orderBy);
}

// Since 2020 all major browsers ensure sort stability with Array.prototype.sort().
// stableSort() brings sort stability to non-modern browsers (notably IE11). If you
// only support modern browsers you can replace stableSort(exampleArray, exampleComparator)
// with exampleArray.slice().sort(exampleComparator)
function stableSort<T>(array, comparator: (a: T, b: T) => number) {
    const stabilizedThis = array.map((el, index) => [el, index] as [T, number]);
    stabilizedThis.sort((a, b) => {
        const order = comparator(a[0], b[0]);
        if (order !== 0) {
            return order;
        }
        return a[1] - b[1];
    });
    return stabilizedThis.map((el) => el[0]);
}

interface HeadCell {
    disablePadding: boolean;
    id: keyof Data;
    label: string;
    numeric: boolean;
}

interface EnhancedTableProps {
    headers: string[];
    onRequestSort: (event: React.MouseEvent<unknown>, property: keyof Data) => void;
    order: Order;
    orderBy: string;
    selHead?: React.ReactNode;
}

function EnhancedTableHead(props: EnhancedTableProps) {
    const { headers, order, orderBy, onRequestSort, selHead = null } = props;
    const createSortHandler = (property: keyof Data) => (event: React.MouseEvent<unknown>) => {
        onRequestSort(event, property);
    };

    const headCells = headers.map((header) => ({
        id: header,
        numeric: false,
        disablePadding: false,
        label: header,
    }));

    return (
        <TableHead>
            <TableRow>
                {selHead}
                {headCells.map((headCell) => (
                    <TableCell
                        key={headCell.id}
                        align={headCell.numeric ? 'right' : 'left'}
                        padding={headCell.disablePadding ? 'none' : 'normal'}
                        sortDirection={orderBy === headCell.id ? order : false}
                    >
                        <TableSortLabel
                            active={orderBy === headCell.id}
                            direction={orderBy === headCell.id ? order : 'asc'}
                            onClick={createSortHandler(headCell.id)}
                        >
                            {headCell.label}
                            {orderBy === headCell.id ? (
                                <Box component='span' sx={visuallyHidden}>
                                    {order === 'desc' ? 'sorted descending' : 'sorted ascending'}
                                </Box>
                            ) : null}
                        </TableSortLabel>
                    </TableCell>
                ))}
            </TableRow>
        </TableHead>
    );
}

const PagedTable = ({ page = 0, rows = [], headers = [], total, onPageChange, pageRows = 10, allRows = null, onExportAll = null, module = "" }) => {
    const [order, setOrder] = React.useState<Order>('asc');
    const [orderBy, setOrderBy] = React.useState('calories');
    const [dense, setDense] = React.useState(true);
    const [rowsPerPage, setRowsPerPage] = React.useState(pageRows);
    const [exporting, setExporting] = React.useState(false);

    const onRequestSort = (event: React.MouseEvent<unknown>, property: keyof Data) => {
        const isAsc = orderBy === property && order === 'asc';
        setOrder(isAsc ? 'desc' : 'asc');
        setOrderBy(property);
    };

    // Avoid a layout jump when reaching the last page with empty rows.
    const emptyRows = rows.length < rowsPerPage ? Math.max(0, rowsPerPage - rows.length) : 0;

    const visibleRows = React.useMemo(
        () => stableSort(rows, getComparator(order, orderBy)),
        [order, orderBy, page, rowsPerPage, rows],
    );

    const hasAllData = allRows && allRows.length > 0;
    const hasExportAllHandler = !!onExportAll;

    const handleExport = React.useCallback(async () => {
        if (onExportAll) {
            setExporting(true);
            try {
                await onExportAll();
            } finally {
                setExporting(false);
            }
        } else if (hasAllData) {
            exportToCSV(allRows, headers, `open-virome-${module || 'SRA'}-PagedTable.csv`);
        } else {
            exportToCSV(rows, headers, `open-virome-${module || 'SRA'}-PagedTable.csv`);
        }
    }, [rows, headers, allRows, hasAllData, onExportAll, module]);

    /* ── 平台扩展：把表格里的 Run 编号转入平台「公共数据下载」──
     * Open-Virome SPA 由平台以同源 iframe 嵌在 /virome，fetch 相对路径
     * /api/dl/create 即命中平台 Flask（独立部署时 404，按钮报“仅平台内可用”）。
     * 每页只渲染 10 行，但勾选跨页保留，方便攒一批再转。 */
    const runIds = React.useMemo(
        () => rows
            .map((row) => rowRun(row, headers))
            .filter((v) => v !== null) as string[],
        [rows, headers],
    );
    const canDownload = runIds.length > 0;

    const [sel, setSel] = React.useState<Set<string>>(new Set());
    const [dlBusy, setDlBusy] = React.useState(false);
    const [dlMsg, setDlMsg] = React.useState('');
    const [dlErr, setDlErr] = React.useState(false);

    /* 2026-11-06：数据源变化（重新检索 / 换批数据）时清空跨页勾选。
     * 以前 sel 只增不减，翻到新结果后仍勾着上一批 Run 编号，
     * 点「转入数据下载」会把已经不在表里的条目一起提交。
     * 用「长度 + 首行 Run 标识」做 key：rows 每次渲染新建引用时不误清，
     * 真正换了一批数据才清。 */
    const rowsKey = React.useMemo(() => {
        if (!rows.length) return '0|';
        const first = rows[0] || {};
        const run = rowRun(rows[0], headers);
        const head = run !== null ? run : String(first[headers[0]] ?? '');
        return `${rows.length}|${head}`;
    }, [rows, headers]);
    React.useEffect(() => {
        setSel(new Set());
    }, [rowsKey]);

    /* pageRows 是外部受控 props；以前只在首次挂载时进 state，父组件
     * 改每页条数后表格仍按旧值分页。 */
    React.useEffect(() => {
        setRowsPerPage(pageRows);
    }, [pageRows]);

    const toggleRow = (run: string, checked: boolean) => {
        setSel((prev) => {
            const next = new Set(prev);
            if (checked) next.add(run);
            else next.delete(run);
            return next;
        });
    };
    const toggleAll = (checked: boolean) => {
        setSel((prev) => {
            const next = new Set(prev);
            runIds.forEach((r) => (checked ? next.add(r) : next.delete(r)));
            return next;
        });
    };

    const postRuns = React.useCallback(async (items: string[]): Promise<void> => {
        const resp = await fetch('/api/dl/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'open-virome_runs', items, concurrency: 2 }),
        });
        if (!resp.ok) {
            let msg = `HTTP ${resp.status}`;
            try {
                msg = (await resp.json())?.error || msg;
            } catch { /* 非 JSON 响应体 */ }
            if (resp.status === 404) {
                msg = '仅在平台内嵌（/virome）时可用 / Only available inside the platform';
            }
            throw new Error(msg);
        }
    }, []);

    const sendToDownloads = React.useCallback(async () => {
        const items = Array.from(sel).filter((v) => RUN_RE.test(v));
        if (!items.length || dlBusy) return;
        setDlBusy(true);
        setDlErr(false);
        try {
            await postRuns(items);
            setDlMsg(`已提交 ${items.length} 个 Run 到平台「公共数据下载」 / Sent ${items.length} run(s) to platform Downloads`);
            setSel(new Set());
        } catch (e) {
            setDlErr(true);
            setDlMsg(String((e as Error)?.message || e));
        } finally {
            setDlBusy(false);
        }
    }, [sel, dlBusy, postRuns]);

    const sendOne = React.useCallback(async (run: string) => {
        setDlBusy(true);
        setDlErr(false);
        try {
            await postRuns([run]);
            setDlMsg(`${run} 已提交到平台「公共数据下载」 / Sent to platform Downloads`);
        } catch (e) {
            setDlErr(true);
            setDlMsg(String((e as Error)?.message || e));
        } finally {
            setDlBusy(false);
        }
    }, [postRuns]);

    const loganOne = React.useCallback(async (run: string) => {
        setDlBusy(true);
        try {
            const res = await fetch('/api/logan/has_assembly', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({accessions: [run]}),
            });
            const d = await res.json();
            if (d && d[run]) {
                try {
                    window.parent.sessionStorage.setItem('vp_prefill',
                        JSON.stringify({page: 'loganfetch', accessions: run, ts: Date.now()}));
                } catch (e) {}
                window.parent.location.href = '/tools?g=resource#t-loganfetch';
                return;
            }
            setDlErr(true);
            setDlMsg(`${run} 无 Logan 组装 / No Logan assembly (GSA/CRR or not indexed)`);
        } catch (e) {
            setDlErr(true);
            setDlMsg(String((e as Error)?.message || e));
        } finally {
            setDlBusy(false);
        }
    }, []);

    const allChecked = canDownload && runIds.every((r) => sel.has(r));
    const selHead = canDownload ? (
        <TableCell padding='checkbox'>
            <Checkbox checked={allChecked} onChange={(e) => toggleAll(e.target.checked)} />
        </TableCell>
    ) : null;

    return (
        <Box sx={{ width: '100%', overflow: 'hidden' }}>
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 0.5, alignItems: 'center', gap: 1 }}>
                {dlMsg ? (
                    <Typography variant='caption'
                                sx={{ mr: 'auto', color: dlErr ? '#c62828' : '#2e7d32' }}>
                        {dlMsg}
                    </Typography>
                ) : null}
                <ExportButton
                    onClick={handleExport}
                    tooltip={exporting ? 'Downloading...' : hasExportAllHandler || hasAllData ? 'Download all as CSV' : 'Download current page as CSV'}
                />
                {canDownload ? (
                    <Button size='small' variant='outlined'
                            disabled={dlBusy || sel.size === 0}
                            onClick={sendToDownloads}>
                        ⬇ 转入数据下载{sel.size ? ` (${sel.size})` : ''}
                    </Button>
                ) : null}
            </Box>
            <Paper sx={{ overflow: 'hidden' }}>
                <TableContainer>
                    <Table aria-labelledby='tableTitle' size={dense ? 'small' : 'medium'}>
                        <EnhancedTableHead
                            headers={headers}
                            order={order}
                            orderBy={orderBy}
                            onRequestSort={onRequestSort}
                            selHead={selHead}
                        />
                        <TableBody>
                            {visibleRows.map((row, index) => {
                                const run = rowRun(row, headers);
                                return (
                                    <TableRow hover tabIndex={-1} key={`${row.id}_${index}`}>
                                        {canDownload ? (
                                            <TableCell padding='checkbox'>
                                                {run ? (
                                                    <Checkbox checked={sel.has(run)}
                                                              onChange={(e) => toggleRow(run, e.target.checked)} />
                                                ) : null}
                                            </TableCell>
                                        ) : null}
                                        {headers.map((header, index) => (
                                            <TableCell key={index} align='left'>
                                                <div
                                                    style={{
                                                        overflow: 'hidden',
                                                        textOverflow: 'ellipsis',
                                                        maxWidth: '200px',
                                                        whiteSpace: 'nowrap',
                                                    }}
                                                >
                                                    {row[header]}
                                                </div>
                                            </TableCell>
                                        ))}
                                        {canDownload && run ? (
                                            <TableCell>
                                                <Tooltip title='转入数据下载 / Send to platform Downloads'>
                                                    <IconButton size='small' disabled={dlBusy}
                                                                onClick={() => sendOne(run)}>
                                                        <DownloadIcon fontSize='small' />
                                                    </IconButton>
                                                </Tooltip>
                                                <Tooltip title='LOGAN 数据下载 / LOGAN data download (check assembly first)'>
                                                    <IconButton size='small' disabled={dlBusy}
                                                                onClick={() => loganOne(run)}>
                                                        <BiotechIcon fontSize='small' />
                                                    </IconButton>
                                                </Tooltip>
                                            </TableCell>
                                        ) : null}
                                    </TableRow>
                                );
                            })}
                            {emptyRows > 0 && (
                                <TableRow
                                    style={{
                                        height: (dense ? 33 : 53) * emptyRows,
                                    }}
                                >
                                    <TableCell colSpan={6} />
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
                <TablePagination
                    rowsPerPageOptions={[10]}
                    component='div'
                    count={total ?? 0}
                    rowsPerPage={rowsPerPage}
                    page={page}
                    onPageChange={onPageChange}
                />
            </Paper>
        </Box>
    );
};

export default PagedTable;
