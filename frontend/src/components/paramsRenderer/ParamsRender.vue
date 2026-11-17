<script setup>
import { InputNumber, Select, Slider, ToggleSwitch } from 'primevue';
import { onMounted, reactive } from 'vue';



const props = defineProps({
  params: { type: Array, required: true }
})



const values = reactive({})


const emit = defineEmits([
    'change',
])

/*
emit(
    'change',
    structuredClone(values)
)*/

onMounted(() => {
    console.log(props.params)
    props.params.forEach(param => {
        values[param.name] = param.default
    })
})

</script>
<template>
    <div
        :key="param.name"
        v-for="param in params"
        class="param-card"
        :class="!param.enabled ? 'disabled' : ''"
    >   
        <h4 class="param-header">{{param?.displayName}} </h4> 
        <div 
            v-if="param.enum" 
        >
            <Select
                v-if="param.enum"
                v-model="values[param.name]"
                :options="param.enum || []"
                :disabled="!param.enabled"
                :placeholder="'Select'"
                show-clear
            />
        </div>
       
        <div 
            v-if="param.type == 'BOOLEAN'" 
        >
            <ToggleSwitch
                v-model="values[param.name]"
                :disabled="!param.enabled"
            />
        </div>
        
        <div 
            v-if="param.type == 'INTEGER' || param.type == 'NUMBER' " 
            class="param-value-box"
        >
            <Slider
                v-model="values[param.name]"
                :min="param.minimum"
                :max="param.maximum"
                :step="param.step ?? 1"
                :disabled="!param.enabled"
            />

            <InputNumber
                v-model="values[param.name]"
                :min="param.minimum"
                :max="param.maximum"
                :step="param.step ?? 1"
                :disabled="!param.enabled"
            />
        </div>
        

    </div>
</template>
<style scoped src="./ParamsRender.css"></style>

