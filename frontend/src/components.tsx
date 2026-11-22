import React from 'react';
import { Button as BaseButton } from './vendor/shadcn/button';
import { Badge } from './vendor/shadcn/badge';
import { Card, CardContent } from './vendor/shadcn/card';
import { Tabs, TabsList, TabsTrigger } from './vendor/shadcn/tabs';
import { Table as AriaTable, TableHeader, Column, TableBody, Row, Cell } from 'react-aria-components';
import { cn } from './compat/cn';
type Icon = React.ElementType;
type ButtonProps = Omit<React.ComponentProps<typeof BaseButton>,'variant'|'size'> & {variant?:'secondary'|'primary';size?:'small'|'default';leadingIcon?:Icon};
export function Button({variant,size,leadingIcon:Icon,children,...props}:ButtonProps){return <BaseButton variant={variant==='secondary'?'outline':'default'} size={size==='small'?'sm':'default'} type="button" {...props}>{Icon&&<Icon/>}{children}</BaseButton>}
export function ButtonLink({href,leadingIcon:Icon,children}: {href:string;leadingIcon?:Icon;children:React.ReactNode}){return <BaseButton asChild><a href={href}>{Icon&&<Icon/>}{children}</a></BaseButton>}
export function Chip({children}: {children:React.ReactNode;color?:string;variant?:string}){return <Badge variant="secondary">{children}</Badge>}
export type Stat={icon:Icon;label:string;value:string;delta?:string;deltaColor?:string};
export function StatCards({stats,className}:{stats:Stat[];className?:string}){return <div className={cn('sf-stat-grid',className)}>{stats.map(({icon:Icon,...s},i)=><Card key={i}><CardContent><div className="sf-stat-label"><span>{s.label}</span><Icon/></div><strong className="sf-stat-value">{s.value}</strong><span className="sf-stat-caption">{s.delta}</span></CardContent></Card>)}</div>}
export function NavItem({viewKey,href,icon:Icon,label,collapsed,isSelected}:{viewKey:string;href:string;icon:Icon;label:string;collapsed:boolean;isSelected:boolean}){return <BaseButton asChild variant={isSelected?'secondary':'ghost'}><a data-view={viewKey} href={href} aria-label={label} aria-current={isSelected?'page':undefined} title={collapsed?label:undefined} className="sf-nav-item"><Icon/><span className={collapsed?'sf-nav-text sf-nav-collapsed':'sf-nav-text'}>{label}</span></a></BaseButton>}
type Option={value:string;label:React.ReactNode;icon?:React.ReactNode;disabled?:boolean;ariaLabel?:string};
export function SegmentedControl({options,value,onChange,disabled,...props}:{options:Option[];value:string;onChange:(v:string)=>void;disabled?:boolean;size?:string;fullWidth?:boolean;variant?:string;'aria-label'?:string}){return <Tabs value={value} onValueChange={onChange} className="sf-segments"><TabsList aria-label={props['aria-label']}>{options.map(o=><TabsTrigger key={o.value} value={o.value} disabled={disabled||o.disabled} aria-label={o.ariaLabel}>{o.icon&&<span className="sf-segment-icon">{o.icon}</span>}{o.label}</TabsTrigger>)}</TabsList></Tabs>}
// React Aria is retained only for accessible table keyboard and collection behavior.
export function Table(props:React.ComponentProps<typeof AriaTable>){return <AriaTable {...props} className="sf-table"/>}
export {TableHeader,Column as TableColumn,TableBody,Row as TableRow,Cell as TableCell};
