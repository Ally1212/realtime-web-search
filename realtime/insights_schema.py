"""Separate API contract; no changes to the existing customer API namespace."""
from .insights_core import VERSION


def openapi():
    ref=lambda name:{'$ref':'#/components/schemas/'+name}
    def op(summary,method='get',schema=None,code='200',params=()):
        v={'summary':summary,'parameters':list(params),'responses':{code:{'description':'成功'},'400':{'description':'参数无效'},'401':{'description':'客户密钥无效'},'403':{'description':'来源权限或外部处理用途不足'},'404':{'description':'记录不存在'},'409':{'description':'版本冲突、任务执行中、标签未确认或预算不足'},'429':{'description':'速率、并发或日调用预留限制'},'503':{'description':'服务不可用'}}}
        if schema:v['requestBody']={'required':True,'content':{'application/json':{'schema':schema}}}
        if method=='post' and summary in ('创建数据集','提交分析任务'):
            v['parameters'].append({'name':'Idempotency-Key','in':'header','required':True,'schema':{'type':'string','maxLength':120}})
        return {method:v}
    pathparam=lambda n:{'name':n,'in':'path','required':True,'schema':{'type':'string'}}
    query=lambda n,s:{'name':n,'in':'query','schema':s}
    root='/v1/insights';dataset=root+'/datasets/{id}'
    paths={root+'/capabilities':op('模块及模型可用性'),root+'/openapi.json':op('模块API合同'),
        root+'/datasets':{**op('当前客户数据集'),**op('创建数据集','post',ref('Dataset'),'201')},
        dataset:op('固定数据集与当前统计',params=[pathparam('id')]),
        dataset+'/records':op('分页原文、标注与复核',params=[pathparam('id'),query('offset',{'type':'integer','minimum':0,'maximum':5000}),query('limit',{'type':'integer','minimum':1,'maximum':100}),query('tag_id',{'type':'string'}),query('unreviewed',{'type':'boolean'})]),
        dataset+'/taxonomy':op('保存并可选确认标签；改变标签清空旧标注','post',ref('Taxonomy'),params=[pathparam('id')]),
        dataset+'/review':op('人工标注与排除记录','post',ref('Review'),params=[pathparam('id')]),
        dataset+'/runs':{**op('分析任务列表',params=[pathparam('id')]),**op('提交分析任务','post',ref('Run'),'202',params=[pathparam('id')])},
        dataset+'/export':op('固定版本完整导出',params=[pathparam('id'),query('format',{'type':'string','enum':['json','jsonl','csv','topics','html','zip','manifest']})]),
        dataset+'/delete':op('删除独立分析数据，不删除原采集记录','post',ref('Revision'),params=[pathparam('id')]),
        root+'/runs/{id}':op('任务状态、调用次数与token记录',params=[pathparam('id')]),
        root+'/runs/{id}/cancel':op('取消任务；在途调用可能计费','post',{'type':'object','additionalProperties':False},params=[pathparam('id')])}
    obj=lambda props,required=():{'type':'object','additionalProperties':False,'properties':props,'required':list(required)}
    string={'type':'string'};revision={'type':'integer','minimum':1};annotations={'type':'array','items':ref('Annotation')}
    schemas={'Dataset':obj({'name':{'type':'string','maxLength':120},'mode':{'type':'string','enum':['search','voc']},'records':{'type':'array','minItems':1,'maxItems':5000,'items':ref('Record')},'filters':obj({**{k:string for k in ('scope','source','query','from','to','country','language')},'request_limit':{'type':'integer','minimum':1,'maximum':100}}),'thread_cap':{'type':'integer','minimum':1,'maximum':500},'allow_external_processing':{'type':'boolean'}},('name',)),
        'Record':obj({**{k:{'type':'string','nullable':True} for k in ('source_id','source','native_id','text_raw','url','brand','market','language','parent_id','published_at','request_id','occurrence_id','content_kind')},'rating':{'type':'number','minimum':1,'maximum':5,'nullable':True}},('text_raw',)),
        'Tag':obj({**{k:string for k in ('id','level1','level2','name','description')},'keywords':{'type':'array','items':string}},('id','level1','level2','name','description')),
        'Annotation':obj({'tag_id':string,'sentiment':{'type':'string','enum':['positive','negative','neutral','mixed','unknown']},'quote':{'type':'string','maxLength':2000},'start':{'type':'integer','minimum':0}},('tag_id','quote')),
        'Taxonomy':obj({'tags':{'type':'array','minItems':1,'maxItems':60,'items':ref('Tag')},'revision':revision,'approve':{'type':'boolean'}},('tags','revision')),
        'Review':obj({'source_id':string,'annotations':annotations,'revision':revision,'exclude':{'type':'boolean'},'note':{'type':'string','maxLength':1000}},('source_id','revision')),
        'Run':obj({'method':{'type':'string','enum':['rules','ai']},'kind':{'type':'string','enum':['tag','draft']},'max_calls':{'type':'integer','minimum':1,'maximum':500},'revision':revision},('revision',)),
        'Revision':obj({'revision':revision},('revision',))}
    schemas['Record']['additionalProperties']=True
    schemas['Record']['description']='额外字段保存在source_fields；不静默丢弃来源字段。'
    schemas['Dataset']['description']='records与filters恰选一项；filters仅支持search模式。输入JSON最大900KB。'
    return {'openapi':'3.0.3','info':{'title':'SearchFlow Text Insights','version':VERSION,'description':'独立分析模块；不采集、不上传。原文、标签、统计与证据分别存储；金额未知，不承诺标注准确率。'},
        'security':[{'bearerAuth':[],'siteAccess':[]}],'paths':paths,
        'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer'},'siteAccess':{'type':'apiKey','in':'header','name':'X-SearchFlow-Access-Key'}},'schemas':schemas}}
