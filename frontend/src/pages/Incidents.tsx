import { useQuery } from '@tanstack/react-query'
import { api, type Incident } from '../api'

const resp: Record<string, string> = { customer: '客户', us: '我方', supplier: '供应商', unknown: '待定' }

export default function Incidents() {
  const { data = [] } = useQuery({ queryKey: ['incidents'], queryFn: () => api<Incident[]>('/incidents') })
  return (
    <div className="overflow-x-auto rounded-lg border bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-100 text-left">
          <tr>
            {['#', '类别', '模型', 'request_id', '摘要', '责任方', '置信度', '依据'].map((h) => (
              <th key={h} className="px-3 py-2">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((i) => (
            <tr key={i.id} className="border-t align-top">
              <td className="px-3 py-2">{i.id}</td>
              <td className="px-3 py-2">{i.category}</td>
              <td className="px-3 py-2">{i.model}</td>
              <td className="px-3 py-2 font-mono">{i.request_id}</td>
              <td className="px-3 py-2">{i.summary}</td>
              <td className="px-3 py-2">{resp[i.responsible] ?? i.responsible}</td>
              <td className="px-3 py-2">{i.confidence}</td>
              <td className="px-3 py-2 text-slate-500">{i.evidence}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.length === 0 && <p className="p-4 text-slate-500">暂无事件</p>}
    </div>
  )
}
